"""Finland THL national monthly updater.

The updater mirrors the shared monthly updater contract while preserving the
THL reporting-group grain.  In particular, two distinct THL reporting groups
that resolve to the same legacy disease concept are rejected rather than
summed.  This prevents parent totals and child series from being double-counted.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.fi import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    FICubeDataError,
    FIFetchSummary,
    FinlandTHLCrawler,
    HISTORY_START_YEAR,
    recent_months,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_FI_THL_TTR"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/fi/finland_national_monthly.csv"


class FIReportingGroupCollisionError(ValueError):
    """Raised when distinct THL series would collapse into one legacy fact."""


@dataclass
class FIUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass
class FIUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


@dataclass(frozen=True)
class FILegacyProjection:
    rows: List[Dict[str, object]]
    skipped_unmapped: int
    unmapped_labels: Tuple[str, ...]


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    text_value = _norm_text(value).replace(",", "")
    if not text_value:
        return None
    try:
        numeric = float(text_value)
    except ValueError:
        return None
    if not numeric.is_integer() or numeric < 0:
        return None
    return int(numeric)


def _parse_date(row: Mapping[str, object]) -> Optional[date]:
    date_text = _norm_text(row.get("Date"))
    if date_text:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            return None
    year = _parse_int(row.get("Year"))
    month = _parse_int(row.get("Month"))
    if year is not None and month is not None and 1 <= month <= 12:
        return date(year, month, 1)
    return None


def _history_months(as_of: date, *, include_provisional: bool) -> List[Tuple[int, int]]:
    current = (as_of.year, as_of.month)
    months: List[Tuple[int, int]] = []
    for year in range(HISTORY_START_YEAR, as_of.year + 1):
        for month in range(1, 13):
            key = (year, month)
            if key > current or (key == current and not include_provisional):
                continue
            months.append(key)
    return months


def build_legacy_projection(
    rows: Sequence[Dict[str, str]],
    mapping_dict: Mapping[str, int],
    *,
    country_id: int,
    source_name: str = DEFAULT_SOURCE_NAME,
) -> FILegacyProjection:
    """Build idempotent legacy rows without aggregating THL source series."""

    projected: Dict[Tuple[datetime, int, int], Dict[str, object]] = {}
    identities: Dict[Tuple[datetime, int, int], Tuple[str, str, int]] = {}
    skipped_unmapped = 0
    unmapped: Set[str] = set()

    for row in rows:
        parsed_date = _parse_date(row)
        cases = _parse_int(row.get("Cases"))
        if parsed_date is None or cases is None:
            continue
        report_time = datetime.combine(
            parsed_date, datetime.min.time(), tzinfo=timezone.utc
        )
        label = _norm_text(row.get("RawDiseaseLabel") or row.get("Disease"))
        code = _norm_text(row.get("DiseaseCode"))
        disease_id = mapping_dict.get(normalize_mapping_key(label)) or mapping_dict.get(
            normalize_mapping_key(code)
        )
        if disease_id is None:
            skipped_unmapped += 1
            if label:
                unmapped.add(label)
            continue

        key = (report_time, int(disease_id), country_id)
        identity = (code, label, cases)
        previous_identity = identities.get(key)
        if previous_identity is not None:
            if previous_identity == identity:
                # Repeated delivery of the exact same source row is idempotent.
                continue
            raise FIReportingGroupCollisionError(
                "Distinct THL reporting groups must not be aggregated into one "
                f"legacy disease record at {parsed_date}: "
                f"{previous_identity[0] or previous_identity[1]!r} and "
                f"{code or label!r} both map to disease_id={disease_id}"
            )

        identities[key] = identity
        metadata = {
            "raw_disease_label": label,
            "reporting_group_code": code,
            "reporting_group_sid": _norm_text(row.get("ReportingGroupSID")),
            "period_type": "monthly",
            "geography": _norm_text(row.get("Geography")) or "All areas",
            "geography_key": _norm_text(row.get("GeographyKey"))
            or "country:FI:national",
            "age": _norm_text(row.get("Age")) or "All ages",
            "sex": _norm_text(row.get("Sex")) or "All sexes",
            "measure": _norm_text(row.get("Measure")) or "Cases",
            "dataset_status": _norm_text(row.get("DatasetStatus")),
            "is_provisional": _norm_text(row.get("IsProvisional")).casefold()
            == "true",
            "source_url": _norm_text(row.get("SourceURL")),
            "query_url": _norm_text(row.get("QueryURL")),
            "retrieved_at": _norm_text(row.get("RetrievedAt")),
            "source_updated_at": _norm_text(row.get("SourceUpdatedAt")),
            "raw_sha256": _norm_text(row.get("RawSHA256")),
            "dimensions_sha256": _norm_text(row.get("DimensionsSHA256")),
            "license": _norm_text(row.get("License")),
            "revision_semantics": "authoritative_revision",
            "death_reporting": "not_provided_by_source",
            "death_reporting_note": (
                "THL National Infectious Diseases Register cube slice reports "
                "case counts, not death counts."
            ),
        }
        projected[key] = {
            "time": report_time,
            "disease_id": int(disease_id),
            "country_id": country_id,
            "cases": cases,
            "deaths": None,
            "data_source": _norm_text(row.get("Source")) or source_name,
            "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            "raw_data": json.dumps(row, ensure_ascii=False, sort_keys=True),
        }

    output = sorted(
        projected.values(),
        key=lambda item: (item["time"], item["disease_id"], item["country_id"]),
    )
    return FILegacyProjection(
        rows=output,
        skipped_unmapped=skipped_unmapped,
        unmapped_labels=tuple(sorted(unmapped)),
    )


class FIMonthlyUpdater:
    """Fetch and import Finland THL national monthly reporting-group rows."""

    ontology_source_id = MAPPING_SOURCE_ID
    series_registered_rows_only = True
    series_geography_key = "country:FI:national"

    def __init__(
        self,
        *,
        country_code: str = "FI",
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
        refresh_recent_months: int = 3,
        include_current_month: bool = False,
    ) -> None:
        self.country_code = country_code.upper()
        self.source_name = source_name
        self.output_csv = Path(output_csv)
        self.refresh_recent_months = max(1, min(24, int(refresh_recent_months)))
        self.include_current_month = bool(include_current_month)

    def _resolve_requested_months(
        self,
        months: Optional[Sequence[Tuple[int, int]]],
        *,
        as_of: date,
        include_provisional: bool,
        backfill_history: bool,
    ) -> List[Tuple[int, int]]:
        if backfill_history:
            return _history_months(as_of, include_provisional=include_provisional)
        requested = (
            set(months)
            if months is not None
            else set(
                recent_months(
                    as_of,
                    self.refresh_recent_months,
                    include_current_month=include_provisional,
                )
            )
        )
        current = (as_of.year, as_of.month)
        return sorted(
            key
            for key in requested
            if key >= (HISTORY_START_YEAR, 1)
            and (key <= current if include_provisional else key < current)
        )

    @staticmethod
    def _rows_cover_months(
        rows: Sequence[Dict[str, str]], months: Sequence[Tuple[int, int]]
    ) -> bool:
        requested = set(months)
        present = {
            (parsed.year, parsed.month)
            for row in rows
            if (parsed := _parse_date(row)) is not None
        }
        return requested.issubset(present)

    @staticmethod
    def _filter_rows_for_months(
        rows: Sequence[Dict[str, str]], months: Sequence[Tuple[int, int]]
    ) -> List[Dict[str, str]]:
        requested = set(months)
        return [
            row
            for row in rows
            if (parsed := _parse_date(row)) is not None
            and (parsed.year, parsed.month) in requested
        ]

    def refresh_source(
        self,
        *,
        source: str = DEFAULT_SCOPE,
        run_external: bool = False,
        force: bool = False,
        months: Optional[Sequence[Tuple[int, int]]] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        include_provisional: Optional[bool] = None,
        backfill_history: bool = False,
        as_of: Optional[date] = None,
    ) -> FIUpdateFetchResult:
        """Refresh THL data using the shared monthly pipeline interface."""

        del run_external, force
        normalized_source = _norm_text(source).casefold().replace("-", "_")
        if normalized_source not in {
            "",
            "all",
            "fi",
            "finland",
            "thl",
            "ttr",
            DEFAULT_SCOPE,
        }:
            raise ValueError(f"Unsupported FI source: {source}")

        effective_date = as_of or datetime.now(timezone.utc).date()
        effective_include_provisional = (
            self.include_current_month
            if include_provisional is None
            else bool(include_provisional)
        )
        requested_months = self._resolve_requested_months(
            months,
            as_of=effective_date,
            include_provisional=effective_include_provisional,
            backfill_history=backfill_history,
        )
        if not requested_months:
            raise ValueError(
                "FI fetch contains no eligible closed months; use "
                "include_provisional=True to request the current month"
            )

        logs: List[str] = []
        actual_raw_dir = (
            Path(raw_dir)
            if raw_dir is not None
            else ROOT / "data/raw" / self.country_code.lower()
        )
        prior_rows: List[Dict[str, str]] = []
        if self.output_csv.exists():
            try:
                prior_rows = self._load_rows(self.output_csv)
                logs.append(f"[cache] loaded {len(prior_rows)} existing FI rows")
            except Exception as exc:
                logs.append(
                    f"[cache] unable to read existing FI CSV: {type(exc).__name__}: {exc}"
                )

        crawler = FinlandTHLCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        live_rows: List[Dict[str, str]] = []
        live_error: Optional[Exception] = None
        try:
            fetch_summary: FIFetchSummary = crawler.crawl_monthly_national(
                self.output_csv,
                months=requested_months,
                backfill_history=backfill_history,
                include_provisional=effective_include_provisional,
                as_of=effective_date,
            )
            live_rows = self._filter_rows_for_months(
                self._load_rows(self.output_csv), requested_months
            )
            logs.append(
                f"[crawler] fetched {fetch_summary.row_count} rows; "
                f"reporting_groups={fetch_summary.reporting_groups_fetched}; "
                f"queries={fetch_summary.query_count}; "
                f"months={len(requested_months)}; latest={fetch_summary.latest_date}"
            )
            if fetch_summary.omitted_provisional_months:
                logs.append(
                    "[policy] omitted "
                    f"{fetch_summary.omitted_provisional_months} provisional/future month(s)"
                )
            if save_raw:
                logs.append(f"[crawler] raw THL responses archived under {actual_raw_dir}")
        except Exception as exc:
            live_error = exc
            logs.append(f"[crawler] live fetch failed: {type(exc).__name__}: {exc}")

        prior_candidate = (
            self._filter_rows_for_months(prior_rows, requested_months)
            if prior_rows and self._rows_cover_months(prior_rows, requested_months)
            else []
        )
        candidates: List[Tuple[str, List[Dict[str, str]], int]] = []
        if live_rows and self._rows_cover_months(live_rows, requested_months):
            candidates.append(("live fetch", live_rows, 1))
        if prior_candidate:
            candidates.append(("previous CSV snapshot", prior_candidate, 0))
        if not candidates:
            if live_error is not None:
                raise live_error
            raise RuntimeError("FI THL crawler produced no complete monthly rows")

        # A complete live response is authoritative.  The prior snapshot is a
        # recovery path only; it must not win merely because it contains more
        # reporting groups from an older cube revision.
        selected_label, selected_rows, _ = max(
            candidates, key=lambda item: (item[2], len(item[1]))
        )
        if selected_label != "live fetch":
            logs.append(
                f"[recovery] using {selected_label} with {len(selected_rows)} rows"
            )
        return FIUpdateFetchResult(
            rows=selected_rows,
            source_latest_date=self._latest_row_date(selected_rows),
            source_csv=self.output_csv,
            script_logs=logs,
        )

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(f"FI crawler output not found: {csv_path}")
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                label = _norm_text(row.get("Disease") or row.get("RawDiseaseLabel"))
                parsed_date = _parse_date(row)
                cases = _parse_int(row.get("Cases"))
                if not label or parsed_date is None or cases is None:
                    continue

                expected_scope = {
                    "Geography": "all areas",
                    "Age": "all ages",
                    "Sex": "all sexes",
                    "Measure": "cases",
                }
                for field, expected in expected_scope.items():
                    actual = _norm_text(row.get(field)).casefold()
                    if actual != expected:
                        raise FICubeDataError(
                            f"FI CSV row {line_number} is outside the national "
                            f"all-population Cases slice: {field}={row.get(field)!r}"
                        )

                normalized = {key: _norm_text(value) for key, value in row.items()}
                normalized.update(
                    {
                        "Date": parsed_date.isoformat(),
                        "RawDiseaseLabel": label,
                        "DiseaseCode": _norm_text(row.get("DiseaseCode")),
                        "Year": str(parsed_date.year),
                        "Month": str(parsed_date.month),
                        "Cases": str(cases),
                        "Source": _norm_text(row.get("Source")) or self.source_name,
                    }
                )
                rows.append(normalized)
        rows.sort(key=lambda row: (row["Date"], row["DiseaseCode"], row["RawDiseaseLabel"]))
        return rows

    @staticmethod
    def _latest_row_date(rows: Sequence[Dict[str, str]]) -> Optional[date]:
        dates = [parsed for row in rows if (parsed := _parse_date(row)) is not None]
        return max(dates) if dates else None

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
            db, self.country_code, source_id=MAPPING_SOURCE_ID
        )

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> FIUpdateImportResult:
        """Idempotently upsert mapped THL rows; never sum source groups."""

        del force
        if not rows:
            return FIUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)
        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)
        projection = build_legacy_projection(
            rows,
            mapping_dict,
            country_id=country_id,
            source_name=self.source_name,
        )
        if projection.rows:
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
                projection.rows,
            )
        if projection.unmapped_labels:
            logger.warning(
                "FI THL unmapped reporting groups | count={} labels={}",
                len(projection.unmapped_labels),
                list(projection.unmapped_labels),
            )
        imported = len(projection.rows)
        logger.info(
            "FI THL monthly import complete | upserted={} skipped_unmapped={}",
            imported,
            projection.skipped_unmapped,
        )
        return FIUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=projection.skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


__all__ = [
    "DEFAULT_OUTPUT_CSV",
    "FIMonthlyUpdater",
    "FIReportingGroupCollisionError",
    "FIUpdateFetchResult",
    "FIUpdateImportResult",
    "FILegacyProjection",
    "MAPPING_SOURCE_ID",
    "build_legacy_projection",
]
