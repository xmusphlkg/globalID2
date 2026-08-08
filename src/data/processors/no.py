"""Norway FHI MSIS monthly updater and legacy projection importer."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Type
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.no import (
    CSV_FIELDNAMES,
    DEFAULT_HISTORY_START_YEAR,
    DEFAULT_REFRESH_RECENT_MONTHS,
    DEFAULT_SOURCE_NAME,
    NOFetchSummary,
    NorwayMSISCrawler,
    effective_target_months,
    previous_closed_month,
    validate_no_national_rows,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_NO_FHI_MSIS"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/no/norway_fhi_msis_monthly.csv"
NORWAY_TIMEZONE = ZoneInfo("Europe/Oslo")


@dataclass
class NOUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class NOUpdateImportResult:
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
    text_value = _norm_text(value).replace(",", "")
    if not text_value or text_value in {"-", "—", "N/A", "na", "null", "None"}:
        return None
    try:
        return int(text_value)
    except ValueError:
        return None


def _parse_date(row: Dict[str, str]) -> Optional[date]:
    raw_date = _norm_text(row.get("Date"))
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            pass
    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month"))
    if year is None or month is None or not 1 <= month <= 12:
        return None
    return date(year, month, 1)


def _norway_today() -> date:
    return datetime.now(NORWAY_TIMEZONE).date()


class NOMonthlyUpdater:
    """Refresh and import national monthly rows from FHI MSIS.

    The default refresh window is the latest three *closed* months.  This both
    avoids partial current-month counts and overwrites recent revisions on every
    ordinary run.  ``include_current_month=True`` is an explicit opt-in that
    emits the open month as provisional.
    """

    ontology_source_id = MAPPING_SOURCE_ID
    series_registered_rows_only = True
    series_geography_key = "country:NO:national"

    def __init__(
        self,
        *,
        country_code: str = "NO",
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
        refresh_recent_months: int = DEFAULT_REFRESH_RECENT_MONTHS,
        full_history_start_year: int = DEFAULT_HISTORY_START_YEAR,
        include_current_month: bool = False,
        crawler_type: Type[NorwayMSISCrawler] = NorwayMSISCrawler,
        today_provider: Callable[[], date] = _norway_today,
    ) -> None:
        self.country_code = country_code.upper()
        self.source_name = source_name
        self.output_csv = Path(output_csv)
        self.refresh_recent_months = max(1, int(refresh_recent_months))
        self.full_history_start_year = int(full_history_start_year)
        self.include_current_month = bool(include_current_month)
        self.crawler_type = crawler_type
        self.today_provider = today_provider

    def _upper_month(self, *, as_of: Optional[date] = None) -> Tuple[int, int]:
        today = as_of or self.today_provider()
        if self.include_current_month:
            return today.year, today.month
        return previous_closed_month(today)

    def _default_recent_months(self, *, as_of: Optional[date] = None) -> List[Tuple[int, int]]:
        upper_year, upper_month = self._upper_month(as_of=as_of)
        months: List[Tuple[int, int]] = []
        year, month = upper_year, upper_month
        for _ in range(self.refresh_recent_months):
            months.append((year, month))
            month -= 1
            if month == 0:
                year -= 1
                month = 12
        return sorted(months)

    def history_months(
        self,
        *,
        start_year: Optional[int] = None,
        end_date: Optional[date] = None,
    ) -> List[Tuple[int, int]]:
        """Return 1977-to-last-closed-month targets by default."""

        today = end_date or self.today_provider()
        return effective_target_months(
            as_of=today,
            include_current_month=self.include_current_month,
            start_year=int(start_year or self.full_history_start_year),
        )

    def _resolve_requested_months(
        self,
        months: Optional[List[Tuple[int, int]]],
        *,
        force: bool,
        as_of: date,
    ) -> List[Tuple[int, int]]:
        if months is None:
            return (
                self.history_months(end_date=as_of)
                if force
                else self._default_recent_months(as_of=as_of)
            )
        return effective_target_months(
            as_of=as_of,
            include_current_month=self.include_current_month,
            months=months,
        )

    @staticmethod
    def _latest_row_date(rows: List[Dict[str, str]]) -> Optional[date]:
        dates = [parsed for row in rows if (parsed := _parse_date(row)) is not None]
        return max(dates, default=None)

    @staticmethod
    def _filter_rows_for_months(
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

    @staticmethod
    def _rows_cover_months(
        rows: List[Dict[str, str]],
        months: List[Tuple[int, int]],
    ) -> bool:
        present = {
            (parsed.year, parsed.month)
            for row in rows
            if (parsed := _parse_date(row)) is not None
        }
        return set(months).issubset(present)

    def refresh_source(
        self,
        *,
        source: str = "fhi_msis",
        run_external: bool = False,
        force: bool = False,
        months: Optional[List[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> NOUpdateFetchResult:
        """Refresh FHI rows while retaining a validated cache fallback."""

        del run_external  # Kept for the shared monthly-updater contract.
        source_key = _norm_text(source).casefold()
        if source_key not in {"all", "fhi_msis", "msis"}:
            raise ValueError(f"Unsupported NO source: {source!r}")

        as_of = self.today_provider()
        requested_months = self._resolve_requested_months(
            months,
            force=force,
            as_of=as_of,
        )
        if not requested_months:
            raise ValueError("NO refresh contains no eligible closed/current months")

        logs: List[str] = []
        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
                logs.append(f"[cache] loaded {len(prior_rows)} rows from existing CSV")
            except Exception as exc:
                logs.append(
                    f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}"
                )

        actual_raw_dir = Path(raw_dir) if raw_dir else ROOT / "data/raw/no"
        crawler = self.crawler_type(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None
        try:
            summary: NOFetchSummary = crawler.crawl_monthly_national(
                self.output_csv,
                months=requested_months,
                include_current_month=self.include_current_month,
                as_of=as_of,
            )
            live_rows = self._filter_rows_for_months(
                self._load_rows(self.output_csv),
                requested_months,
            )
            validate_no_national_rows(
                live_rows,
                target_months=set(requested_months),
                as_of=as_of,
                include_current_month=self.include_current_month,
            )
            logs.append(
                f"[crawler] prepared {summary.row_count} rows; "
                f"years={summary.years_fetched}; diagnoses={summary.diagnoses_requested}; "
                f"latest={summary.latest_date}; contract={summary.contract_version}"
            )
            if save_raw:
                logs.append(f"[crawler] raw JSON provenance archived under {actual_raw_dir}")
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        prior_candidate = (
            self._filter_rows_for_months(prior_rows, requested_months)
            if prior_rows and self._rows_cover_months(prior_rows, requested_months)
            else []
        )
        if prior_candidate:
            try:
                validate_no_national_rows(
                    prior_candidate,
                    target_months=set(requested_months),
                    as_of=as_of,
                    include_current_month=self.include_current_month,
                )
            except ValueError as exc:
                logs.append(f"[cache] rejected previous CSV snapshot: {exc}")
                prior_candidate = []

        # Prefer every validated live response even when it has fewer rows than
        # the cache.  FHI omits a diagnosis-year after a revision to all-zero,
        # so choosing by row count would resurrect stale cases.
        if live_rows:
            selected_label = "live fetch"
            selected_rows = live_rows
        elif prior_candidate:
            selected_label = "previous CSV snapshot"
            selected_rows = prior_candidate
        else:
            if live_error is not None:
                raise live_error
            raise RuntimeError("NO FHI MSIS crawler produced no usable rows")
        if selected_label != "live fetch":
            logs.append(f"[recovery] using {selected_label} with {len(selected_rows)} rows")

        return NOUpdateFetchResult(
            rows=selected_rows,
            source_latest_date=self._latest_row_date(selected_rows),
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(f"NO crawler output not found: {csv_path}")

        rows: List[Dict[str, str]] = []
        today = self.today_provider()
        current_key = (today.year, today.month)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw_row in csv.DictReader(handle):
                report_date = _parse_date(raw_row)
                label = _norm_text(
                    raw_row.get("RawDiseaseLabel") or raw_row.get("Disease")
                )
                code = _norm_text(raw_row.get("DiseaseCode"))
                cases = _parse_int(raw_row.get("Cases"))
                if report_date is None or not label or not code or cases is None or cases < 0:
                    continue
                status = _norm_text(raw_row.get("DataStatus"))
                if not status:
                    status = (
                        "provisional"
                        if (report_date.year, report_date.month) == current_key
                        else "closed"
                    )
                row = {name: _norm_text(raw_row.get(name)) for name in CSV_FIELDNAMES}
                row.update(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": label,
                        "DiseaseCode": code,
                        "DiseaseGroup": _norm_text(raw_row.get("DiseaseGroup")),
                        "Year": str(report_date.year),
                        "Month": str(report_date.month),
                        "Cases": str(cases),
                        "Deaths": "",
                        "ReportingArea": _norm_text(raw_row.get("ReportingArea"))
                        or "Norway national",
                        "DataStatus": status,
                        "AuthoritativeRevision": "true",
                        "UpdateMode": (
                            "dynamic_provisional"
                            if status == "provisional"
                            else "authoritative_revision"
                        ),
                        "Source": _norm_text(raw_row.get("Source")) or self.source_name,
                        "SourceScope": _norm_text(raw_row.get("SourceScope"))
                        or "fhi_msis",
                    }
                )
                rows.append(row)
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        return rows

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
        maximum = result.scalar()
        return maximum.date() if maximum is not None else None

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
    ) -> NOUpdateImportResult:
        """Upsert mapped national monthly facts, including revision zeroes."""

        del force  # Revisions are always upserted for every fetched month.
        if not rows:
            return NOUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)

        validate_no_national_rows(
            rows,
            as_of=self.today_provider(),
            include_current_month=self.include_current_month,
        )
        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)

        grouped: Dict[Tuple[datetime, int, int], Dict[str, object]] = {}
        skipped_unmapped = 0
        for row in rows:
            report_date = _parse_date(row)
            if report_date is None:
                continue
            label = _norm_text(row.get("RawDiseaseLabel"))
            code = _norm_text(row.get("DiseaseCode"))
            disease_id = mapping_dict.get(
                normalize_mapping_key(label)
            ) or mapping_dict.get(normalize_mapping_key(code))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            cases = _parse_int(row.get("Cases"))
            if cases is None or cases < 0:
                continue
            report_time = datetime(
                report_date.year,
                report_date.month,
                1,
                tzinfo=timezone.utc,
            )
            key = (report_time, disease_id, country_id)
            bucket = grouped.setdefault(
                key,
                {
                    "time": report_time,
                    "disease_id": disease_id,
                    "country_id": country_id,
                    "cases": 0,
                    "data_source": self.source_name,
                    "raw_disease_labels": [],
                    "disease_codes": [],
                    "disease_groups": [],
                    "source_urls": [],
                    "raw_artifacts": [],
                    "raw_sha256": [],
                    "data_statuses": [],
                    "retrieved_at": [],
                    "raw_rows": [],
                },
            )
            bucket["cases"] = int(bucket["cases"]) + cases
            for bucket_key, value in (
                ("raw_disease_labels", label),
                ("disease_codes", code),
                ("disease_groups", _norm_text(row.get("DiseaseGroup"))),
                ("source_urls", _norm_text(row.get("SourceURL"))),
                ("raw_artifacts", _norm_text(row.get("RawArtifact"))),
                ("raw_sha256", _norm_text(row.get("RawSHA256"))),
                ("data_statuses", _norm_text(row.get("DataStatus"))),
                ("retrieved_at", _norm_text(row.get("RetrievedAt"))),
            ):
                target = bucket[bucket_key]
                if value and value not in target:
                    target.append(value)
            bucket["raw_rows"].append(row)

        upsert_rows: List[Dict[str, object]] = []
        for bucket in grouped.values():
            metadata = {
                "source_scope": "fhi_msis",
                "reporting_area": "Norway national",
                "raw_disease_labels": bucket["raw_disease_labels"],
                "disease_codes": bucket["disease_codes"],
                "disease_groups": bucket["disease_groups"],
                "data_statuses": bucket["data_statuses"],
                "source_urls": bucket["source_urls"],
                "retrieved_at": bucket["retrieved_at"],
                "raw_artifacts": bucket["raw_artifacts"],
                "raw_sha256": bucket["raw_sha256"],
                "death_reporting": "not_provided_by_source",
                "death_reporting_note": (
                    "FHI MSIS monthly diagnosis endpoint reports cases, not deaths."
                ),
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
            "NO FHI MSIS monthly import complete: upserted {} rows, skipped_unmapped {}",
            imported,
            skipped_unmapped,
        )
        return NOUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


__all__ = [
    "DEFAULT_OUTPUT_CSV",
    "MAPPING_SOURCE_ID",
    "NOMonthlyUpdater",
    "NOUpdateFetchResult",
    "NOUpdateImportResult",
]
