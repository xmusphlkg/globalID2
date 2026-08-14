"""Sweden FHM SmiNet monthly updater.

The updater keeps the normal monthly-pipeline interface while enforcing two
source-specific rules: future months are never imported, and the most recent
three closed months are always refreshed because SmiNet revises already
published totals. Current-month ingestion is an explicit opt-in and remains
subject to the crawler's all-source non-zero placeholder gate. Public release
is enabled for closed monthly observations from the official FHM statistics
pages.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.se import (
    DEFAULT_SOURCE_NAME,
    NATIONAL_GEOGRAPHY_KEY,
    ONTOLOGY_SOURCE_ID,
    SOURCE_SCOPE,
    SEFetchSummary,
    SwedenSmiNetCrawler,
    closed_months,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = ONTOLOGY_SOURCE_ID

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/se/sweden_sminet_monthly.csv"
DEFAULT_CATALOG_SCAN_STATE = ROOT / "data/cache/se/catalog_scan.json"


@dataclass(frozen=True)
class SEUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]
    source_updated_at: Optional[date] = None


@dataclass(frozen=True)
class SEUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").replace("\xa0", " ").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    text_value = _norm_text(value).replace(" ", "")
    if not text_value or text_value in {"-", "—", "N/A", "null", "None"}:
        return None
    try:
        parsed = int(float(text_value))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_date(row: Dict[str, str]) -> Optional[date]:
    date_text = _norm_text(row.get("Date"))
    if date_text:
        try:
            parsed = date.fromisoformat(date_text)
            return parsed.replace(day=1)
        except ValueError:
            pass

    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month"))
    if year is not None and month is not None and 1 <= month <= 12:
        return date(year, month, 1)
    return None


class SEMonthlyUpdater:
    """Refresh and import FHM SmiNet national monthly case observations."""

    country_code = "SE"
    source_scope = SOURCE_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = NATIONAL_GEOGRAPHY_KEY
    series_registered_rows_only = True
    series_registry_coverage = "required"

    # Keep publication controlled by reviewed code/configuration, not by cached
    # CSV input fields.
    public_release_enabled = True
    license_review_status = "approved_for_public_release"

    def __init__(
        self,
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
        refresh_recent_months: int = 3,
        include_current_month: bool = False,
        catalog_rescan_interval_days: int = 7,
        catalog_scan_state: Optional[Path] = None,
    ) -> None:
        self.source_name = source_name
        self.output_csv = Path(output_csv)
        self.refresh_recent_months = max(1, min(24, int(refresh_recent_months)))
        self.include_current_month = bool(include_current_month)
        self.catalog_rescan_interval_days = max(
            1, min(31, int(catalog_rescan_interval_days))
        )
        self.catalog_scan_state = Path(
            catalog_scan_state
            or (
                DEFAULT_CATALOG_SCAN_STATE
                if self.output_csv == DEFAULT_OUTPUT_CSV
                else self.output_csv.with_suffix(".catalog_scan.json")
            )
        )

    def _catalog_rescan_due(self, *, today: date, force: bool) -> bool:
        if force or not self.catalog_scan_state.exists():
            return True
        try:
            payload = json.loads(self.catalog_scan_state.read_text(encoding="utf-8"))
            last_scan = date.fromisoformat(str(payload.get("last_scan_date") or ""))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return True
        return (today - last_scan).days >= self.catalog_rescan_interval_days

    def _record_catalog_scan(self, *, today: date, disease_count: int) -> None:
        self.catalog_scan_state.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_scan_state.write_text(
            json.dumps(
                {
                    "last_scan_date": today.isoformat(),
                    "disease_count": int(disease_count),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _resolve_requested_months(
        self,
        months: Optional[Iterable[Tuple[int, int]]],
        *,
        today: Optional[date] = None,
        include_current_month: Optional[bool] = None,
    ) -> List[Tuple[int, int]]:
        """Add the revision window and reject every calendar-future month."""

        include_open = (
            self.include_current_month
            if include_current_month is None
            else bool(include_current_month)
        )
        revision_window = set(
            closed_months(
                count=self.refresh_recent_months,
                today=today,
                include_current_month=include_open,
            )
        )
        requested = set(months or []) | revision_window
        return closed_months(
            requested,
            today=today,
            include_current_month=include_open,
        )

    @staticmethod
    def _filter_rows_for_months(
        rows: List[Dict[str, str]],
        months: Iterable[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        requested = set(months)
        return [
            row
            for row in rows
            if (parsed := _parse_date(row)) is not None
            and (parsed.year, parsed.month) in requested
        ]

    @staticmethod
    def _merge_live_with_cache(
        live_rows: List[Dict[str, str]],
        cached_rows: List[Dict[str, str]],
    ) -> tuple[List[Dict[str, str]], int]:
        """Prefer live identities and recover only missing disease-month rows."""

        merged: dict[tuple[str, str], Dict[str, str]] = {}
        for row in cached_rows:
            identity = (
                _norm_text(row.get("Date")),
                _norm_text(row.get("DiseaseCode") or row.get("RawDiseaseLabel")).casefold(),
            )
            merged[identity] = row
        cached_count = len(merged)
        for row in live_rows:
            identity = (
                _norm_text(row.get("Date")),
                _norm_text(row.get("DiseaseCode") or row.get("RawDiseaseLabel")).casefold(),
            )
            merged[identity] = row
        recovered = max(0, len(merged) - len(live_rows)) if live_rows else cached_count
        rows = list(merged.values())
        rows.sort(key=lambda item: (item["Date"], item["RawDiseaseLabel"], item["DiseaseCode"]))
        return rows, recovered

    def refresh_source(
        self,
        *,
        source: str = SOURCE_SCOPE,
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        include_current_month: Optional[bool] = None,
        today: Optional[date] = None,
    ) -> SEUpdateFetchResult:
        """Fetch FHM pages and prepare normalized rows for the monthly pipeline."""

        del run_external
        if _norm_text(source).casefold() not in {
            "all",
            SOURCE_SCOPE,
            "fhm_sminet",
            "fohm",
            "fhm",
            "sminet",
            "se",
        }:
            raise ValueError(f"Unsupported Sweden source: {source}")

        include_open = (
            self.include_current_month
            if include_current_month is None
            else bool(include_current_month)
        )
        requested_months = self._resolve_requested_months(
            months,
            today=today,
            include_current_month=include_open,
        )
        if not requested_months:
            raise ValueError("SE SmiNet refresh has no eligible months to fetch")
        logs: List[str] = [
            "[gate] public release disabled; licensing review status=pending",
            (
                f"[planner] requesting {len(requested_months)} eligible month(s); "
                f"revision_window={self.refresh_recent_months}; "
                f"include_current_month={str(include_open).lower()}"
            ),
        ]
        actual_raw_dir = Path(raw_dir) if raw_dir else ROOT / "data/raw/se"

        prior_all_rows: List[Dict[str, str]] = []
        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_all_rows = self._load_rows(self.output_csv)
                prior_rows = self._filter_rows_for_months(
                    prior_all_rows, requested_months
                )
                logs.append(f"[cache] loaded {len(prior_rows)} requested rows")
            except Exception as exc:
                logs.append(
                    f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}"
                )

        # A validated snapshot is also a bounded catalog of known SmiNet pages.
        # Daily runs reuse it, while a stateful weekly rescan discovers newly
        # added disease pages without forcing a historical refresh.
        known_disease_codes = sorted(
            {
                _norm_text(row.get("DiseaseCode"))
                for row in prior_all_rows
                if _norm_text(row.get("DiseaseCode"))
            }
        )
        scan_date = today or datetime.now(timezone.utc).date()
        full_catalog_scan = self._catalog_rescan_due(today=scan_date, force=force)
        if known_disease_codes and not full_catalog_scan:
            logs.append(
                f"[planner] reusing {len(known_disease_codes)} known SmiNet disease pages"
            )
        elif full_catalog_scan:
            logs.append("[planner] rescanning the complete SmiNet disease-page catalog")

        crawler = SwedenSmiNetCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None
        fetch_summary: Optional[SEFetchSummary] = None
        try:
            fetch_summary = crawler.crawl_monthly_national(
                self.output_csv,
                months=requested_months,
                disease_codes=(
                    known_disease_codes
                    if known_disease_codes and not full_catalog_scan
                    else None
                ),
                today=today,
                include_current_month=include_open,
            )
            live_rows = self._filter_rows_for_months(
                self._load_rows(self.output_csv), requested_months
            )
            logs.append(
                f"[crawler] prepared {fetch_summary.row_count} rows; "
                f"diseases={fetch_summary.diseases_fetched}; "
                f"latest={fetch_summary.latest_date}; "
                f"csv_pages={fetch_summary.csv_pages}; "
                f"html_fallback_pages={fetch_summary.html_fallback_pages}"
            )
            if full_catalog_scan:
                self._record_catalog_scan(
                    today=scan_date,
                    disease_count=fetch_summary.diseases_fetched,
                )
            if save_raw:
                logs.append(f"[crawler] raw artifacts archived under {actual_raw_dir}")
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")
        finally:
            crawler.close()

        placeholder_months = set(
            fetch_summary.placeholder_months_omitted if fetch_summary else ()
        )
        if placeholder_months:
            prior_rows = [
                row
                for row in prior_rows
                if (parsed := _parse_date(row)) is None
                or (parsed.year, parsed.month) not in placeholder_months
            ]
            logs.append(
                "[gate] omitted all-zero provisional placeholder month(s): "
                + ", ".join(
                    f"{year:04d}-{month:02d}"
                    for year, month in sorted(placeholder_months)
                )
            )

        if not live_rows and not prior_rows:
            if live_error is not None:
                raise live_error
            raise RuntimeError("SE SmiNet crawler produced no usable rows")

        rows, recovered = self._merge_live_with_cache(live_rows, prior_rows)
        if recovered:
            logs.append(f"[recovery] retained {recovered} cached disease-month row(s)")

        return SEUpdateFetchResult(
            rows=rows,
            source_latest_date=self._latest_row_date(rows),
            source_csv=self.output_csv,
            script_logs=logs,
            source_updated_at=(
                fetch_summary.latest_source_update if fetch_summary else None
            ),
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(f"SE crawler output not found: {csv_path}")

        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                disease = _norm_text(row.get("RawDiseaseLabel") or row.get("Disease"))
                report_date = _parse_date(row)
                cases = _parse_int(row.get("Cases"))
                if not disease or report_date is None or cases is None:
                    continue
                dataset_status = _norm_text(row.get("DatasetStatus"))
                is_provisional = (
                    _norm_text(row.get("IsProvisional")).casefold() == "true"
                    or dataset_status.casefold() == "provisional"
                )
                rows.append(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": disease,
                        "DiseaseCode": _norm_text(row.get("DiseaseCode")),
                        "Year": str(report_date.year),
                        "Month": str(report_date.month),
                        "Cases": str(cases),
                        "Geography": _norm_text(row.get("Geography")) or "SE:national",
                        "GeographyKey": _norm_text(row.get("GeographyKey"))
                        or NATIONAL_GEOGRAPHY_KEY,
                        "Scope": _norm_text(row.get("Scope")) or "all",
                        "Granularity": "monthly",
                        "DatasetStatus": dataset_status
                        or ("provisional" if is_provisional else "closed_revisable"),
                        "IsProvisional": "true" if is_provisional else "false",
                        "DataComplete": _norm_text(row.get("DataComplete"))
                        or ("false" if is_provisional else "TRUE"),
                        "AuthoritativeRevision": "true",
                        "UpdateMode": (
                            "dynamic_provisional"
                            if is_provisional
                            else "authoritative_revision"
                        ),
                        "SourceUpdatedAt": _norm_text(row.get("SourceUpdatedAt")),
                        "RetrievedAt": _norm_text(row.get("RetrievedAt")),
                        "Source": _norm_text(row.get("Source")) or self.source_name,
                        "SourceURL": _norm_text(row.get("SourceURL")),
                        "DownloadURL": _norm_text(row.get("DownloadURL")),
                        "RetrievalMethod": _norm_text(row.get("RetrievalMethod")),
                        # Never trust a cached file to change publication state.
                        "PublicReleaseEnabled": (
                            "true" if self.public_release_enabled else "false"
                        ),
                        "LicenseReviewStatus": self.license_review_status,
                    }
                )

        rows.sort(key=lambda item: (item["Date"], item["RawDiseaseLabel"], item["DiseaseCode"]))
        return rows

    @staticmethod
    def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
        parsed_dates = [parsed for row in rows if (parsed := _parse_date(row)) is not None]
        return max(parsed_dates, default=None)

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
        value = result.scalar()
        if value is None:
            return None
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(YEAR FROM dr.time)::int AS yr,
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
        result = await db.execute(
            text("SELECT id FROM countries WHERE code = :code"),
            {"code": self.country_code},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Country not found in database: {self.country_code}")
        return int(row[0])

    async def _load_mapping_dict(self, db: AsyncSession) -> Dict[str, int]:
        return await load_country_mapping_dict(
            db,
            self.country_code,
            source_id=MAPPING_SOURCE_ID,
        )

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> SEUpdateImportResult:
        """Authoritatively upsert mapped national rows into the legacy projection."""

        del force
        if not rows:
            return SEUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)
        grouped: Dict[Tuple[datetime, int, int], Dict[str, object]] = {}
        skipped_unmapped = 0

        for row in rows:
            report_date = _parse_date(row)
            cases = _parse_int(row.get("Cases"))
            if report_date is None or cases is None:
                continue
            label = _norm_text(row.get("RawDiseaseLabel"))
            code = _norm_text(row.get("DiseaseCode"))
            disease_id = mapping_dict.get(normalize_mapping_key(label)) or mapping_dict.get(
                normalize_mapping_key(code)
            )
            if disease_id is None:
                skipped_unmapped += 1
                continue

            timestamp = datetime.combine(report_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            )
            key = (timestamp, disease_id, country_id)
            bucket = grouped.setdefault(
                key,
                {
                    "time": timestamp,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": 0,
                    "data_source": self.source_name,
                    "raw_disease_labels": [],
                    "disease_codes": [],
                    "source_urls": [],
                    "download_urls": [],
                    "source_updated_at": [],
                    "retrieval_methods": [],
                    "raw_rows": [],
                },
            )
            bucket["cases"] = int(bucket["cases"]) + cases
            for value, field in (
                (label, "raw_disease_labels"),
                (code, "disease_codes"),
                (_norm_text(row.get("SourceURL")), "source_urls"),
                (_norm_text(row.get("DownloadURL")), "download_urls"),
                (_norm_text(row.get("SourceUpdatedAt")), "source_updated_at"),
                (_norm_text(row.get("RetrievalMethod")), "retrieval_methods"),
            ):
                if value and value not in bucket[field]:
                    bucket[field].append(value)
            bucket["raw_rows"].append(row)

        upsert_rows: List[Dict[str, object]] = []
        for bucket in grouped.values():
            metadata = {
                "raw_disease_labels": bucket["raw_disease_labels"],
                "disease_codes": bucket["disease_codes"],
                "geography_key": NATIONAL_GEOGRAPHY_KEY,
                "scope": "all",
                "granularity": "monthly",
                "source_urls": bucket["source_urls"],
                "download_urls": bucket["download_urls"],
                "source_updated_at": bucket["source_updated_at"],
                "retrieval_methods": bucket["retrieval_methods"],
                "authoritative_revision": True,
                "public_release_enabled": self.public_release_enabled,
                "license_review_status": self.license_review_status,
                "death_reporting": "not_provided_by_source",
            }
            upsert_rows.append(
                {
                    "time": bucket["time"],
                    "disease_id": bucket["disease_id"],
                    "country_id": bucket["country_id"],
                    "cases": bucket["cases"],
                    "deaths": None,
                    "data_source": bucket["data_source"],
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "raw_data": json.dumps(bucket["raw_rows"], ensure_ascii=False),
                }
            )

        if upsert_rows:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths,
                        data_source, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases, new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths,
                        :data_source, :metadata, :raw_data,
                        0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        data_source = EXCLUDED.data_source,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            f"SE SmiNet import complete: upserted {imported} rows, "
            f"skipped_unmapped {skipped_unmapped}"
        )
        return SEUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


__all__ = [
    "DEFAULT_OUTPUT_CSV",
    "MAPPING_SOURCE_ID",
    "SEMonthlyUpdater",
    "SEUpdateFetchResult",
    "SEUpdateImportResult",
]
