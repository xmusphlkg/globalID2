"""Ireland HPSC national weekly updater.

This updater exposes the same fetch/import contract used by the existing crawl
service while preserving HPSC's source-native weekly series.  The legacy table
receives only reviewed disease mappings; every registered source series is
written losslessly through the series-first store by ``CrawlService``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.ie import (
    DEFAULT_HISTORY_START,
    DEFAULT_REFRESH_RECENT_WEEKS,
    DEFAULT_SOURCE_NAME,
    DEFAULT_SOURCE_SCOPE,
    IEFetchSummary,
    IEWeek,
    IrelandHPSCWeeklyCrawler,
    iter_iso_weeks,
    recent_source_weeks,
    stable_disease_code,
    validate_national_rows,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_IE_HPSC_NDH"
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/ie/ireland_hpsc_weekly.csv"


class IESourceSeriesCollisionError(ValueError):
    """Raised when distinct HPSC series collapse into one legacy fact."""


@dataclass(frozen=True)
class IEUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]
    periods_fetched: Tuple[Tuple[int, int], ...]
    source_updated_at: Optional[str] = None


@dataclass(frozen=True)
class IEUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").replace("\xa0", " ").split()).strip()


def _parse_date(row: Mapping[str, object]) -> Optional[date]:
    value = _norm_text(row.get("Date"))
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    iso = parsed.isocalendar()
    if parsed.weekday() != 0:
        return None
    try:
        row_year = int(_norm_text(row.get("Year")))
        row_week = int(_norm_text(row.get("Week")))
    except ValueError:
        return None
    if (iso.year, iso.week) != (row_year, row_week):
        return None
    return parsed


def _parse_cases(value: object) -> Optional[int]:
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


def build_legacy_projection(
    rows: Sequence[Dict[str, str]],
    mapping_dict: Mapping[str, int],
    *,
    country_id: int,
    source_name: str = DEFAULT_SOURCE_NAME,
) -> Tuple[List[Dict[str, object]], int, Tuple[str, ...]]:
    """Build national weekly legacy rows without cross-series summation."""

    projected: Dict[Tuple[datetime, int, int], Dict[str, object]] = {}
    identities: Dict[Tuple[datetime, int, int], Tuple[str, str, int]] = {}
    skipped_unmapped = 0
    unmapped: Set[str] = set()

    for row in rows:
        report_date = _parse_date(row)
        cases = _parse_cases(row.get("Cases"))
        if report_date is None or cases is None:
            continue
        label = _norm_text(row.get("RawDiseaseLabel"))
        code = _norm_text(row.get("DiseaseCode"))
        disease_id = mapping_dict.get(normalize_mapping_key(label)) or mapping_dict.get(
            normalize_mapping_key(code)
        )
        if disease_id is None:
            skipped_unmapped += 1
            if label:
                unmapped.add(label)
            continue

        report_time = datetime.combine(
            report_date, datetime.min.time(), tzinfo=timezone.utc
        )
        key = (report_time, int(disease_id), country_id)
        identity = (code, label, cases)
        previous = identities.get(key)
        if previous is not None:
            if previous == identity:
                continue
            raise IESourceSeriesCollisionError(
                "Distinct HPSC weekly source series must not be added into one "
                f"legacy record at {report_date}: {previous[1]!r} and {label!r} "
                f"both map to disease_id={disease_id}"
            )
        identities[key] = identity

        metadata = {
            "raw_disease_label": label,
            "disease_code": code,
            "period_type": "weekly",
            "iso_year": int(_norm_text(row.get("Year"))),
            "iso_week": int(_norm_text(row.get("Week"))),
            "year_week": _norm_text(row.get("YearWeek")),
            "geography_key": _norm_text(row.get("GeographyKey"))
            or "country:IE:national",
            "reporting_area": _norm_text(row.get("ReportingArea"))
            or "Ireland national",
            "dataset_status": _norm_text(row.get("DatasetStatus")),
            "value_status": _norm_text(row.get("ValueStatus")),
            "source_scope": _norm_text(row.get("SourceScope"))
            or DEFAULT_SOURCE_SCOPE,
            "source_url": _norm_text(row.get("SourceURL")),
            "portal_url": _norm_text(row.get("PortalURL")),
            "retrieved_at": _norm_text(row.get("RetrievedAt")),
            "source_updated_at": _norm_text(row.get("SourceUpdatedAt")),
            "source_contract": _norm_text(row.get("SourceContract")),
            "object_id": _norm_text(row.get("ObjectId")),
            "unique_id": _norm_text(row.get("UniqueId")),
            "raw_artifact": _norm_text(row.get("RawArtifact")),
            "raw_sha256": _norm_text(row.get("RawSHA256")),
            "authoritative_revision": True,
            "public_release_enabled": False,
            "license_review_status": "written_permission_required",
            "death_reporting": "not_provided_by_source",
            "death_reporting_note": (
                "HPSC Notifiable Diseases Hub weekly table reports case counts, "
                "not death counts."
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
    return output, skipped_unmapped, tuple(sorted(unmapped))


class IEWeeklyUpdater:
    """Refresh and import HPSC national weekly notifiable-disease rows."""

    country_code = "IE"
    source_scope = DEFAULT_SOURCE_SCOPE
    ontology_source_id = MAPPING_SOURCE_ID
    series_geography_key = "country:IE:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    license_review_status = "written_permission_required"

    def __init__(
        self,
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
        refresh_recent_weeks: int = DEFAULT_REFRESH_RECENT_WEEKS,
        full_history_start_year: int = DEFAULT_HISTORY_START[0],
    ) -> None:
        self.source_name = source_name
        self.output_csv = Path(output_csv)
        self.refresh_recent_weeks = max(1, min(104, int(refresh_recent_weeks)))
        self.full_history_start_year = max(
            DEFAULT_HISTORY_START[0], int(full_history_start_year)
        )

    @staticmethod
    def _period_key(row: Mapping[str, object]) -> Optional[Tuple[int, int]]:
        parsed = _parse_date(row)
        if parsed is None:
            return None
        iso = parsed.isocalendar()
        return iso.year, iso.week

    @staticmethod
    def _filter_rows_for_periods(
        rows: Sequence[Dict[str, str]],
        periods: Iterable[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        requested = set(periods)
        return [
            row
            for row in rows
            if (period := IEWeeklyUpdater._period_key(row)) is not None
            and period in requested
        ]

    def _load_rows(self, csv_path: Path) -> List[Dict[str, str]]:
        if not csv_path.exists():
            raise FileNotFoundError(f"IE crawler output not found: {csv_path}")
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                report_date = _parse_date(row)
                label = _norm_text(row.get("RawDiseaseLabel"))
                code = _norm_text(row.get("DiseaseCode"))
                if report_date is None or not label or code != stable_disease_code(label):
                    continue
                cases = _parse_cases(row.get("Cases"))
                value_status = _norm_text(row.get("ValueStatus"))
                if cases is None and value_status != "missing":
                    continue
                normalized = {key: _norm_text(value) for key, value in row.items()}
                normalized.update(
                    {
                        "Date": report_date.isoformat(),
                        "RawDiseaseLabel": label,
                        "DiseaseCode": code,
                        "Cases": "" if cases is None else str(cases),
                        "Source": _norm_text(row.get("Source")) or self.source_name,
                        "SourceScope": DEFAULT_SOURCE_SCOPE,
                        "GeographyKey": "country:IE:national",
                        "PublicReleaseEnabled": "false",
                        "LicenseReviewStatus": self.license_review_status,
                    }
                )
                rows.append(normalized)
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        if rows:
            validate_national_rows(rows)
        return rows

    def refresh_source(
        self,
        *,
        source: str = DEFAULT_SOURCE_SCOPE,
        run_external: bool = False,
        force: bool = False,
        fill_missing: bool = False,
        existing_weeks: Optional[Set[Tuple[int, int]]] = None,
        start_year: Optional[int] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> IEUpdateFetchResult:
        """Fetch the revision window, missing weeks, or full HPSC history."""

        del run_external
        if _norm_text(source).casefold() not in {
            "all",
            DEFAULT_SOURCE_SCOPE,
            "hpsc",
            "ndh",
            "ie",
            "ireland",
        }:
            raise ValueError(f"Unsupported Ireland source: {source}")

        selected_start_year = max(
            DEFAULT_HISTORY_START[0], int(start_year or self.full_history_start_year)
        )
        actual_raw_dir = Path(raw_dir) if raw_dir else ROOT / "data/raw/ie"
        crawler = IrelandHPSCWeeklyCrawler(
            save_raw=save_raw,
            raw_dir=actual_raw_dir,
        )
        logs = [
            "[gate] public release disabled; HPSC written permission required",
            f"[planner] refresh_recent_weeks={self.refresh_recent_weeks}",
        ]
        planned_weeks: Optional[List[Tuple[int, int]]] = None
        try:
            if force or fill_missing:
                source_earliest, source_latest = crawler.fetch_source_bounds()
                effective_earliest = source_earliest
                if selected_start_year > source_earliest.year:
                    effective_earliest = IEWeek.from_parts(selected_start_year, 1)
                all_periods = iter_iso_weeks(effective_earliest, source_latest)
                if force:
                    selected = set(all_periods)
                    logs.append(
                        f"[planner] full history from {effective_earliest.source_label} "
                        f"to {source_latest.source_label}"
                    )
                else:
                    existing = set(existing_weeks or set())
                    selected = {
                        period
                        for period in all_periods
                        if (period.year, period.week) not in existing
                    }
                    selected.update(
                        recent_source_weeks(
                            source_latest, self.refresh_recent_weeks
                        )
                    )
                    logs.append(
                        f"[planner] missing_weeks={len(selected)} including revision window"
                    )
                planned_weeks = sorted(
                    (period.year, period.week) for period in selected
                )

            summary: IEFetchSummary = crawler.crawl_weekly_national(
                self.output_csv,
                weeks=planned_weeks,
                start_year=selected_start_year,
                refresh_recent_weeks=self.refresh_recent_weeks,
            )
        finally:
            crawler.session.close()

        all_rows = self._load_rows(self.output_csv)
        rows = self._filter_rows_for_periods(all_rows, summary.periods_fetched)
        validate_national_rows(rows, requested_weeks=set(summary.periods_fetched))
        logs.append(
            f"[crawler] prepared {summary.row_count} rows across "
            f"{summary.weeks_fetched} weeks; diseases={summary.diseases_catalogued}; "
            f"latest={summary.latest_date}"
        )
        if save_raw:
            logs.append(f"[crawler] raw ArcGIS envelopes archived under {actual_raw_dir}")
        return IEUpdateFetchResult(
            rows=rows,
            source_latest_date=summary.latest_date,
            source_csv=self.output_csv,
            script_logs=logs,
            periods_fetched=summary.periods_fetched,
            source_updated_at=summary.source_updated_at,
        )

    @staticmethod
    def _latest_row_date(rows: Sequence[Dict[str, str]]) -> Optional[date]:
        return max(
            (parsed for row in rows if (parsed := _parse_date(row)) is not None),
            default=None,
        )

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

    async def get_db_weeks(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(ISOYEAR FROM dr.time)::int AS yr,
                    EXTRACT(WEEK FROM dr.time)::int AS wk
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
    ) -> IEUpdateImportResult:
        """Upsert reviewed HPSC mappings into the legacy compatibility table."""

        del force
        if not rows:
            return IEUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)
        validate_national_rows(rows)
        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)
        projection, skipped_unmapped, unmapped_labels = build_legacy_projection(
            rows,
            mapping_dict,
            country_id=country_id,
            source_name=self.source_name,
        )
        if projection:
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
                projection,
            )
        if unmapped_labels:
            logger.warning(
                "IE HPSC unmapped source series | count={} labels={}",
                len(unmapped_labels),
                list(unmapped_labels),
            )
        imported = len(projection)
        logger.info(
            "IE HPSC weekly import complete | upserted={} skipped_unmapped={}",
            imported,
            skipped_unmapped,
        )
        return IEUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


__all__ = [
    "DEFAULT_OUTPUT_CSV",
    "IESourceSeriesCollisionError",
    "IEUpdateFetchResult",
    "IEUpdateImportResult",
    "IEWeeklyUpdater",
    "MAPPING_SOURCE_ID",
    "build_legacy_projection",
]
