"""AU monthly updater.

AU disease data is dynamically revised — the NINDSS counts for past months
are updated as more notifications come in.  Therefore every run upserts all
returned rows unconditionally (no date-gate), and the default fetch window is
the most recent 3 months.  When ``fill_missing=True`` the service layer also
back-fills any (year, month) pairs absent from the database.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers import AustraliaNINDSSCrawler
from src.data.crawlers.au import (
    AUFetchSummary,
    AU_STATE_SUBDIVISIONS,
    normalize_au_state_code,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/au/australia_national_data.csv"
DEFAULT_SUBDIVISION_OUTPUT_DIR = ROOT / "data/current/au/subdivisions"
DEFAULT_SOURCE_NAME = "Australia NINDSS (location aggregated)"
MAPPING_SOURCE_ID = "SRC_AU_NINDSS"
_ARCHIVE_SKIP_STATES = {"AUS", "UNKNOWN", "TOTAL", "ALL"}


@dataclass
class AUUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class AUUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value)
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def _parse_date(row: Dict[str, str]) -> Optional[date]:
    date_text = _norm_text(row.get("Date"))
    if date_text:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(date_text, fmt).date()
            except ValueError:
                pass

    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month"))
    if year is not None and month is not None and 1 <= month <= 12:
        return date(year, month, 1)
    return None


class AUMonthlyUpdater:
    """Read AU national monthly rows from local crawler output and import."""

    # The legacy disease mapping is complete, while lossless source-series
    # registrations are intentionally introduced a subset at a time.
    ontology_source_id = MAPPING_SOURCE_ID
    series_registered_rows_only = True

    def __init__(
        self,
        *,
        country_code: str = "AU",
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path | None = None,
    ) -> None:
        self.country_code = country_code.upper()
        if self.country_code != "AU" and self.country_code not in AU_STATE_SUBDIVISIONS:
            raise ValueError(
                f"Unsupported Australian state/territory jurisdiction: {country_code}"
            )
        self.parent_country_code = "AU" if self.country_code != "AU" else None
        self.mapping_country_code = "AU"
        self.series_country_code = "AU"
        self.series_geography_key = f"country:{self.country_code}:national"
        self.source_name = source_name
        self.output_csv = (
            Path(output_csv)
            if output_csv is not None
            else self._default_output_csv(self.country_code)
        )

    @staticmethod
    def _default_output_csv(country_code: str) -> Path:
        normalized = str(country_code or "").strip().upper()
        if normalized == "AU":
            return DEFAULT_OUTPUT_CSV
        return DEFAULT_SUBDIVISION_OUTPUT_DIR / f"{normalized.lower()}_nindss_monthly.csv"

    @staticmethod
    def _default_raw_archive_root(country_code: str) -> Path:
        normalized = str(country_code or "").strip().upper()
        if normalized == "AU":
            return ROOT / "data/raw/au"
        return ROOT / "data/raw/au/subdivisions" / normalized.lower()

    @property
    def is_subdivision(self) -> bool:
        return self.country_code != "AU"

    @staticmethod
    def _default_recent_months() -> List[Tuple[int, int]]:
        now = datetime.now()
        months_to_fetch: List[Tuple[int, int]] = []
        for delta in range(3):
            month = now.month - delta
            year = now.year
            if month <= 0:
                month += 12
                year -= 1
            months_to_fetch.append((year, month))
        return sorted(set(months_to_fetch))

    def _resolve_requested_months(
        self, months: Optional[List[Tuple[int, int]]]
    ) -> List[Tuple[int, int]]:
        return sorted(set(months)) if months is not None else self._default_recent_months()

    def _rows_cover_months(
        self,
        rows: List[Dict[str, str]],
        months: List[Tuple[int, int]],
    ) -> bool:
        requested = set(months)
        present = {
            (parsed.year, parsed.month)
            for row in rows
            if (parsed := _parse_date(row)) is not None
        }
        return requested.issubset(present)

    def _filter_rows_for_months(
        self,
        rows: List[Dict[str, str]],
        months: List[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        requested = set(months)
        return [
            row
            for row in rows
            if (parsed := _parse_date(row)) is not None
            and (parsed.year, parsed.month) in requested
        ]

    def _write_rows_to_output_csv(self, rows: List[Dict[str, str]]) -> AUFetchSummary:
        ordered_rows = sorted(rows, key=lambda r: (r["Date"], r["RawDiseaseLabel"]))
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "",
            "Disease",
            "DiseaseFull",
            "Group",
            "Year",
            "Month",
            "Date",
            "Cases",
            "Population",
            "Incidence",
            "JurisdictionCode",
            "ParentCountryCode",
            "LocationType",
            "ReportingArea",
            "Geocode",
            "GeographyKey",
        ]

        latest_date = self._latest_row_date(ordered_rows)
        with self.output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for idx, row in enumerate(ordered_rows, start=1):
                parsed_date = _parse_date(row)
                if parsed_date is None:
                    continue
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": row["RawDiseaseLabel"],
                        "DiseaseFull": row.get("DiseaseFull") or row["RawDiseaseLabel"],
                        "Group": row.get("Group", ""),
                        "Year": str(parsed_date.year),
                        "Month": str(parsed_date.month),
                        "Date": parsed_date.isoformat(),
                        "Cases": str(max(0, _parse_int(row.get("Cases")) or 0)),
                        "Population": row.get("Population", ""),
                        "Incidence": row.get("Incidence", ""),
                        "JurisdictionCode": row.get("JurisdictionCode", "AU"),
                        "ParentCountryCode": row.get("ParentCountryCode", ""),
                        "LocationType": row.get("LocationType", "country"),
                        "ReportingArea": row.get("ReportingArea", "Australia"),
                        "Geocode": row.get("Geocode", "AU"),
                        "GeographyKey": row.get("GeographyKey", "country:AU:national"),
                    }
                )

        return AUFetchSummary(
            row_count=len(ordered_rows),
            latest_date=latest_date,
            csv_url=self.source_name,
        )

    def _build_rows_from_raw_archive(
        self,
        archive_root: Path,
        months: List[Tuple[int, int]],
    ) -> Optional[List[Dict[str, str]]]:
        if not archive_root.exists():
            return None

        restored_rows: List[Dict[str, str]] = []
        for year, month in months:
            month_dir = archive_root / f"{year}" / f"{month:02d}"
            files = sorted(month_dir.glob("*.json"))
            if not files:
                return None

            for archive_file in files:
                try:
                    payload = json.loads(archive_file.read_text(encoding="utf-8"))
                except Exception:
                    return None

                disease = _norm_text(payload.get("disease")) or _norm_text(
                    payload.get("Disease")
                )
                parsed_counts = payload.get("parsed_counts")
                if not disease or not isinstance(parsed_counts, dict):
                    return None

                if self.is_subdivision:
                    state_value = None
                    for raw_state, raw_value in parsed_counts.items():
                        if normalize_au_state_code(raw_state) == self.country_code:
                            state_value = raw_value
                            break
                    if state_value is None:
                        return None
                    parsed_value = _parse_int(state_value)
                    if parsed_value is None:
                        continue
                    restored_rows.append(
                        self._normalized_output_row(
                            year=year,
                            month=month,
                            disease=disease,
                            cases=max(0, parsed_value),
                            source_file=archive_file.name,
                        )
                    )
                else:
                    national_total = 0
                    for state, value in parsed_counts.items():
                        if _norm_text(state).upper() in _ARCHIVE_SKIP_STATES:
                            continue
                        parsed_value = _parse_int(value)
                        if parsed_value is not None:
                            national_total += parsed_value

                    restored_rows.append(
                        self._normalized_output_row(
                            year=year,
                            month=month,
                            disease=disease,
                            cases=max(0, national_total),
                            source_file=archive_file.name,
                        )
                    )

        restored_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        return restored_rows

    def _normalized_output_row(
        self,
        *,
        year: int,
        month: int,
        disease: str,
        cases: int,
        source_file: str,
    ) -> Dict[str, str]:
        if self.is_subdivision:
            meta = AU_STATE_SUBDIVISIONS[self.country_code]
            return {
                "Date": date(year, month, 1).isoformat(),
                "RawDiseaseLabel": disease,
                "DiseaseFull": disease,
                "Cases": str(max(0, cases)),
                "Group": "state_territory_total",
                "Incidence": "",
                "Population": "",
                "Source": self.source_name,
                "JurisdictionCode": self.country_code,
                "ParentCountryCode": "AU",
                "LocationType": "subdivision",
                "ReportingArea": meta["name"],
                "Geocode": self.country_code,
                "GeographyKey": self.series_geography_key,
                "__source_file": source_file,
            }
        return {
            "Date": date(year, month, 1).isoformat(),
            "RawDiseaseLabel": disease,
            "DiseaseFull": disease,
            "Cases": str(max(0, cases)),
            "Group": "national_total",
            "Incidence": "",
            "Population": "",
            "Source": self.source_name,
            "JurisdictionCode": "AU",
            "ParentCountryCode": "",
            "LocationType": "country",
            "ReportingArea": "Australia",
            "Geocode": "AU",
            "GeographyKey": "country:AU:national",
            "__source_file": source_file,
        }

    def refresh_source(
        self,
        *,
        source: str = "au",
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> AUUpdateFetchResult:
        """Fetch AU data from the NINDSS Power BI dashboard.

        Args:
            source:       Ignored (kept for interface parity with JP/US).
            run_external: Ignored (Playwright runs in-process).
            force:        Re-fetch even if data appears up-to-date.
            months:       Explicit (year, month) pairs to request.  When None
                          the crawler defaults to the most recent 3 months.
        """
        logs: List[str] = []
        requested_months = self._resolve_requested_months(months)
        archive_root = (
            Path(raw_dir)
            if raw_dir is not None
            else self._default_raw_archive_root(self.country_code)
        )
        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
            except Exception as exc:
                logs.append(f"[cache] unable to read existing CSV snapshot: {type(exc).__name__}: {exc}")

        crawler = AustraliaNINDSSCrawler(
            save_raw=save_raw,
            raw_dir=archive_root if save_raw else raw_dir,
        )
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None

        try:
            if self.is_subdivision:
                fetch_summary = crawler.crawl_monthly_subdivision_csv(
                    self.output_csv,
                    jurisdiction_code=self.country_code,
                    months=months,
                )
            else:
                fetch_summary = crawler.crawl_monthly_national_csv(
                    self.output_csv,
                    months=months,
                )
            logs.append(
                f"[crawler] fetched {fetch_summary.row_count} rows; "
                f"months={'all 3 recent' if months is None else len(months)}; "
                f"latest={fetch_summary.latest_date}"
            )
            if save_raw and raw_dir is not None:
                logs.append(f"[crawler] raw archived under {raw_dir}")
            live_rows = self._filter_rows_for_months(self._load_rows(self.output_csv), requested_months)
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        archive_rows = self._build_rows_from_raw_archive(archive_root, requested_months)
        archive_candidate = (
            self._filter_rows_for_months(archive_rows, requested_months)
            if archive_rows and self._rows_cover_months(archive_rows, requested_months)
            else []
        )
        prior_candidate = (
            self._filter_rows_for_months(prior_rows, requested_months)
            if prior_rows and self._rows_cover_months(prior_rows, requested_months)
            else []
        )

        candidates: List[Tuple[str, List[Dict[str, str]], int]] = []
        if live_rows:
            candidates.append(("live fetch", live_rows, 2))
        if archive_candidate:
            candidates.append(("raw archive", archive_candidate, 1))
        if prior_candidate:
            candidates.append(("previous CSV snapshot", prior_candidate, 0))

        if not candidates:
            if live_error is not None:
                raise live_error
            raise RuntimeError("AU crawler produced no usable rows")

        selected_label, rows, _ = max(candidates, key=lambda item: (len(item[1]), item[2]))

        if selected_label != "live fetch":
            summary = self._write_rows_to_output_csv(rows)
            logs.append(
                f"[recovery] using {selected_label} with {summary.row_count} rows "
                f"for {len(requested_months)} requested months"
            )

        latest = self._latest_row_date(rows)

        return AUUpdateFetchResult(
            rows=rows,
            source_latest_date=latest,
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"AU crawler output not found: {csv_path}. "
                "Please run the AU crawler in globalID2 first."
            )
        rows: List[Dict[str, str]] = []
        today = datetime.now(timezone.utc).date()
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                disease = _norm_text(row.get("Disease"))
                report_date = _parse_date(row)
                cases = _parse_int(row.get("Cases"))
                if not disease or report_date is None or cases is None:
                    continue

                rows.append(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": disease,
                        "DiseaseFull": _norm_text(row.get("DiseaseFull")) or disease,
                        "Cases": str(max(0, cases)),
                        "Group": _norm_text(row.get("Group")),
                        "Incidence": _norm_text(row.get("Incidence")),
                        "Population": _norm_text(row.get("Population")),
                        "Source": self.source_name,
                        # NNDSS values can be retrospectively revised.  Only an
                        # open calendar month is incomplete by construction.
                        "DatasetStatus": (
                            "provisional"
                            if (report_date.year, report_date.month)
                            == (today.year, today.month)
                            else "closed_revisable"
                        ),
                        "IsProvisional": (
                            "true"
                            if (report_date.year, report_date.month)
                            == (today.year, today.month)
                            else "false"
                        ),
                        "JurisdictionCode": _norm_text(row.get("JurisdictionCode"))
                        or self.country_code,
                        "ParentCountryCode": _norm_text(row.get("ParentCountryCode")),
                        "LocationType": _norm_text(row.get("LocationType"))
                        or ("subdivision" if self.is_subdivision else "country"),
                        "ReportingArea": _norm_text(row.get("ReportingArea"))
                        or (
                            AU_STATE_SUBDIVISIONS[self.country_code]["name"]
                            if self.is_subdivision
                            else "Australia"
                        ),
                        "Geocode": _norm_text(row.get("Geocode")) or self.country_code,
                        "GeographyKey": _norm_text(row.get("GeographyKey"))
                        or self.series_geography_key,
                        "__source_file": csv_path.name,
                    }
                )

        rows.sort(key=lambda r: (r["Date"], r["RawDiseaseLabel"]))
        if self.is_subdivision:
            self._validate_subdivision_rows(rows)
        return rows

    def _validate_subdivision_rows(self, rows: List[Dict[str, str]]) -> None:
        expected_area = AU_STATE_SUBDIVISIONS[self.country_code]["name"]
        for row_number, row in enumerate(rows, start=2):
            if row.get("JurisdictionCode") != self.country_code:
                raise ValueError(
                    f"AU subdivision row {row_number} has unexpected jurisdiction"
                )
            if row.get("ParentCountryCode") != "AU":
                raise ValueError(
                    f"AU subdivision row {row_number} has unexpected parent country"
                )
            if row.get("LocationType") != "subdivision":
                raise ValueError(
                    f"AU subdivision row {row_number} has unexpected location type"
                )
            if row.get("Geocode") != self.country_code:
                raise ValueError(f"AU subdivision row {row_number} has unexpected geocode")
            if row.get("GeographyKey") != self.series_geography_key:
                raise ValueError(
                    f"AU subdivision row {row_number} has unexpected geography"
                )
            if row.get("ReportingArea") != expected_area:
                raise ValueError(
                    f"AU subdivision row {row_number} has unexpected reporting area"
                )

    @staticmethod
    def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
        latest: Optional[date] = None
        for row in rows:
            try:
                day = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if latest is None or day > latest:
                latest = day
        return latest

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        result = await db.execute(
            text(
                """
                SELECT MAX(dr.time)
                FROM disease_records dr
                JOIN countries c ON c.id = dr.country_id
                WHERE c.code = :code
                """
            ),
            {"code": self.country_code},
        )
        max_time = result.scalar()
        if max_time is None:
            return None
        return max_time.date()

    async def get_db_months(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        """Return the set of (year, month) pairs already in the disease_records table for AU."""
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(YEAR  FROM dr.time)::int AS yr,
                    EXTRACT(MONTH FROM dr.time)::int AS mo
                FROM disease_records dr
                JOIN countries c ON c.id = dr.country_id
                WHERE c.code = :code
                """
            ),
            {"code": self.country_code},
        )
        return {(int(row[0]), int(row[1])) for row in result.fetchall()}

    async def _get_country_id(self, db: AsyncSession) -> int:
        result = await db.execute(text("SELECT id FROM countries WHERE code = :code"), {"code": self.country_code})
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Country not found in database: {self.country_code}")
        return int(row[0])

    async def _load_mapping_dict(self, db: AsyncSession) -> Dict[str, int]:
        return await load_country_mapping_dict(
            db, self.mapping_country_code, source_id=MAPPING_SOURCE_ID
        )

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> AUUpdateImportResult:
        """
        Upsert AU disease rows into the database.

        AU data is dynamically revised, so ALL rows returned by the crawler
        are always upserted (ON CONFLICT DO UPDATE) regardless of whether
        they are older than ``db_latest_date``.  The ``force`` flag and
        ``db_latest_date`` are kept for interface parity with JP/US but do
        not gate which rows are written.
        """
        if not rows:
            return AUUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        upsert_rows: List[Dict[str, object]] = []
        skipped_unmapped = 0
        seen_keys: set[Tuple[datetime, int, int]] = set()

        for row in rows:
            try:
                day = datetime.strptime(row.get("Date", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            # AU data is dynamically revised — always upsert, never skip by date.

            label = _norm_text(row.get("RawDiseaseLabel", ""))
            disease_id = mapping_dict.get(normalize_mapping_key(label))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases", ""))
            incidence = None
            try:
                incidence = float(row.get("Incidence", "")) if row.get("Incidence", "").strip() else None
            except ValueError:
                incidence = None

            metadata_obj = {
                "raw_disease_label": label,
                "disease_full": row.get("DiseaseFull", ""),
                "group": row.get("Group", ""),
                "population": row.get("Population", ""),
                "source_file": row.get("__source_file", ""),
                "jurisdiction_code": row.get("JurisdictionCode", self.country_code),
                "parent_country_code": row.get("ParentCountryCode", ""),
                "location_type": row.get("LocationType", ""),
                "reporting_area": row.get("ReportingArea", ""),
                "geography_key": row.get("GeographyKey", self.series_geography_key),
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": "Australia NNDSS notification feed used here reports cases, not death counts.",
            }

            key = (day, disease_id, country_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            upsert_rows.append(
                {
                    "time": day,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": cases if cases is not None else 0,
                    "deaths": None,
                    "region": row.get("ReportingArea") if self.is_subdivision else None,
                    "data_source": row.get("Source", self.source_name),
                    "incidence_rate": incidence,
                    "metadata": json.dumps(metadata_obj),
                    "raw_data": json.dumps(row),
                }
            )

        if upsert_rows:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths, region,
                        data_source, incidence_rate, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths, :region,
                        :data_source, :incidence_rate, :metadata, :raw_data,
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        region = EXCLUDED.region,
                        data_source = EXCLUDED.data_source,
                        incidence_rate = EXCLUDED.incidence_rate,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            "AU monthly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )

        return AUUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


# Backward compatibility for older imports.
AUWeeklyUpdater = AUMonthlyUpdater
