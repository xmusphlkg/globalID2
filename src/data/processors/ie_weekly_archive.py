"""Ireland HPSC Lenus weekly-archive updater."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.ie import stable_disease_code
from src.data.crawlers.ie_weekly_archive import (
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_SOURCE_NAME,
    DEFAULT_ARCHIVE_SOURCE_SCOPE,
    DEFAULT_ARCHIVE_START_YEAR,
    IrelandHPSCWeeklyArchiveCrawler,
    validate_archive_rows,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARCHIVE_OUTPUT_CSV = ROOT / "data/current/ie/ireland_hpsc_weekly_archive.csv"
ARCHIVE_ONTOLOGY_SOURCE_ID = "SRC_IE_HPSC_WEEKLY_ARCHIVE"


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split()).strip()


@dataclass(frozen=True)
class IEWeeklyArchiveFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    coverage_path: Path
    script_logs: List[str]
    periods_fetched: Tuple[Tuple[int, int], ...]
    catalogue_periods: Tuple[Tuple[int, int], ...]
    missing_periods: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class IEWeeklyArchiveImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


class IEWeeklyArchiveUpdater:
    """Persist the archive as its own historical weekly source series."""

    country_code = "IE"
    source_scope = DEFAULT_ARCHIVE_SOURCE_SCOPE
    ontology_source_id = ARCHIVE_ONTOLOGY_SOURCE_ID
    series_geography_key = "country:IE:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    license_review_status = "not_checked_for_ingestion"
    full_history_start_year = DEFAULT_ARCHIVE_START_YEAR
    full_history_end_year = DEFAULT_ARCHIVE_END[0]

    def __init__(
        self,
        *,
        source_name: str = DEFAULT_ARCHIVE_SOURCE_NAME,
        output_csv: Path = DEFAULT_ARCHIVE_OUTPUT_CSV,
    ) -> None:
        self.source_name = source_name
        self.output_csv = Path(output_csv)

    def _load_rows(self) -> List[Dict[str, str]]:
        if not self.output_csv.exists():
            raise FileNotFoundError(f"IE weekly archive output not found: {self.output_csv}")
        rows: List[Dict[str, str]] = []
        with self.output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                label, code = _text(raw.get("RawDiseaseLabel")), _text(raw.get("DiseaseCode"))
                if not label or code != stable_disease_code(label):
                    continue
                normalized = {key: _text(value) for key, value in raw.items()}
                normalized.update(
                    {
                        "RawDiseaseLabel": label,
                        "DiseaseCode": code,
                        "Source": _text(raw.get("Source")) or self.source_name,
                        "SourceScope": DEFAULT_ARCHIVE_SOURCE_SCOPE,
                        "GeographyKey": "country:IE:national",
                        "PublicReleaseEnabled": "false",
                        "LicenseReviewStatus": _text(raw.get("LicenseReviewStatus")) or self.license_review_status,
                    }
                )
                rows.append(normalized)
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        validate_archive_rows(rows)
        return rows

    def _existing_source_periods(self) -> Set[Tuple[int, int]]:
        if not self.output_csv.exists():
            return set()
        periods: Set[Tuple[int, int]] = set()
        with self.output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = _text(row.get("SourceReport"))
                if len(value) == 8 and value[4:6] == "-W":
                    try:
                        periods.add((int(value[:4]), int(value[6:])))
                    except ValueError:
                        continue
        return periods

    def refresh_source(
        self,
        *,
        source: str = DEFAULT_ARCHIVE_SOURCE_SCOPE,
        run_external: bool = False,
        force: bool = False,
        fill_missing: bool = False,
        existing_weeks: Optional[Set[Tuple[int, int]]] = None,
        start_year: Optional[int] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> IEWeeklyArchiveFetchResult:
        del run_external
        if _text(source).casefold() not in {
            DEFAULT_ARCHIVE_SOURCE_SCOPE, "weekly_archive", "hpsc_archive", "archive"
        }:
            raise ValueError(f"Unsupported Ireland weekly archive source: {source}")
        selected_start = max(
            DEFAULT_ARCHIVE_START_YEAR,
            min(int(start_year or DEFAULT_ARCHIVE_START_YEAR), DEFAULT_ARCHIVE_END[0]),
        )
        actual_raw_dir = Path(raw_dir) if raw_dir else ROOT / "data/raw/ie/weekly_archive"
        crawler = IrelandHPSCWeeklyArchiveCrawler(save_raw=save_raw, raw_dir=actual_raw_dir)
        try:
            catalogue = tuple(
                report for report in crawler.discover_catalogue()
                if report.year >= selected_start
                and (report.year, report.week) <= DEFAULT_ARCHIVE_END
            )
            catalogue_periods = {(report.year, report.week) for report in catalogue}
            if force:
                selected = catalogue_periods
                mode = "full catalogued weekly archive rebuild"
            elif fill_missing:
                selected = catalogue_periods - (
                    set(existing_weeks or set()) | self._existing_source_periods()
                )
                mode = "catalogued archive weeks missing from the source-series store"
            else:
                selected = catalogue_periods
                mode = "immutable catalogue reconciliation"
            if not selected:
                expected: Set[Tuple[int, int]] = set()
                cursor = date.fromisocalendar(selected_start, 1, 1)
                boundary = date.fromisocalendar(*DEFAULT_ARCHIVE_END, 1)
                while cursor <= boundary:
                    iso = cursor.isocalendar()
                    expected.add((iso.year, iso.week))
                    cursor += timedelta(days=7)
                latest = max((report.monday for report in catalogue), default=None)
                return IEWeeklyArchiveFetchResult(
                    rows=[], source_latest_date=latest, source_csv=self.output_csv,
                    coverage_path=self.output_csv.with_suffix(".coverage.json"),
                    script_logs=["[planner] no catalogued archive weeks require fetching"],
                    periods_fetched=(), catalogue_periods=tuple(sorted(catalogue_periods)),
                    missing_periods=tuple(sorted(expected - catalogue_periods)),
                )
            summary = crawler.crawl_weekly_archive(
                self.output_csv,
                periods=sorted(selected),
                start_year=selected_start,
                catalogue=catalogue,
            )
        finally:
            crawler.session.close()

        selected_labels = {f"{year:04d}-W{week:02d}" for year, week in selected}
        rows = [
            row for row in self._load_rows()
            if row.get("SourceReport") in selected_labels
        ]
        validate_archive_rows(rows, requested_periods=set(selected))
        logs = [
            "[licence] validation skipped for ingestion; public release disabled",
            f"[planner] {mode}",
            (
                f"[crawler] prepared {summary.row_count} rows across "
                f"{len(summary.periods_fetched)} archived reports; "
                f"catalogue_missing={len(summary.missing_periods)}; "
                "missing reports remain unknown and are never zero-filled"
            ),
        ]
        if save_raw:
            logs.append(f"[crawler] raw PDFs archived under {actual_raw_dir}")
        return IEWeeklyArchiveFetchResult(
            rows=rows,
            source_latest_date=summary.latest_date,
            source_csv=self.output_csv,
            coverage_path=summary.coverage_path,
            script_logs=logs,
            periods_fetched=summary.periods_fetched,
            catalogue_periods=summary.catalogue_periods,
            missing_periods=summary.missing_periods,
        )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        result = await db.execute(
            text(
                """
                SELECT MAX(o.time)
                FROM disease_series_observations o
                JOIN disease_surveillance_series s ON s.series_code = o.series_code
                WHERE s.country_code = :country_code AND s.source_system = :source_system
                """
            ),
            {"country_code": self.country_code, "source_system": self.ontology_source_id},
        )
        value = result.scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_weeks(self, db: AsyncSession) -> Set[Tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT EXTRACT(ISOYEAR FROM o.time)::int, EXTRACT(WEEK FROM o.time)::int
                FROM disease_series_observations o
                JOIN disease_surveillance_series s ON s.series_code = o.series_code
                WHERE s.country_code = :country_code AND s.source_system = :source_system
                """
            ),
            {"country_code": self.country_code, "source_system": self.ontology_source_id},
        )
        return {(int(row[0]), int(row[1])) for row in result.fetchall()}

    async def import_rows(
        self,
        db: AsyncSession,
        rows: List[Dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> IEWeeklyArchiveImportResult:
        """Keep provisional PDF snapshots outside the legacy merged fact table."""

        del db, force
        if not rows:
            return IEWeeklyArchiveImportResult(0, 0, db_latest_date, source_latest_date, False)
        validate_archive_rows(rows)
        return IEWeeklyArchiveImportResult(
            inserted_or_updated=len(rows),
            skipped_unmapped=0,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=True,
        )


__all__ = [
    "ARCHIVE_ONTOLOGY_SOURCE_ID", "DEFAULT_ARCHIVE_OUTPUT_CSV",
    "IEWeeklyArchiveFetchResult", "IEWeeklyArchiveImportResult",
    "IEWeeklyArchiveUpdater",
]
