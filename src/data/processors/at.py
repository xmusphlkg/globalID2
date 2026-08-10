"""Austria AGES Radar importer with lossless source-series retention."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.at import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID, AustriaAGESRadarCrawler

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/at/austria_ages_radar_monthly.csv"
MAPPING_SOURCE_ID = ONTOLOGY_SOURCE_ID


@dataclass(frozen=True)
class ATUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass(frozen=True)
class ATUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split())


def _parse_date(row: Dict[str, str]) -> Optional[date]:
    try:
        return date.fromisoformat(_text(row.get("Date")))
    except ValueError:
        return None


def _parse_cases(row: Dict[str, str]) -> Optional[int]:
    try:
        value = int(_text(row.get("Cases")).replace(",", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class ATMonthlyUpdater:
    """AGES update contract: complete source history plus a three-month overwrite window."""

    country_code = "AT"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_geography_key = "country:AT:national"
    public_release_enabled = False
    license_review_status = "pending"

    def __init__(self, *, output_csv: Path = DEFAULT_OUTPUT_CSV, refresh_recent_months: int = 3, crawler_type=AustriaAGESRadarCrawler) -> None:
        self.output_csv = Path(output_csv)
        self.refresh_recent_months = max(1, min(12, int(refresh_recent_months)))
        self.crawler_type = crawler_type

    @staticmethod
    def _recent_months(today: date, count: int) -> List[tuple[int, int]]:
        year, month = today.year, today.month - 1
        result = []
        for _ in range(count):
            if month == 0:
                year, month = year - 1, 12
            result.append((year, month)); month -= 1
        return sorted(result)

    def _load_rows(self, path: Path) -> List[Dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [{key: _text(value) for key, value in row.items()} for row in csv.DictReader(handle) if _parse_date(row) and _parse_cases(row) is not None]

    def refresh_source(self, *, source: str = DEFAULT_SCOPE, run_external: bool = False, force: bool = False, months: Optional[Sequence[tuple[int, int]]] = None, save_raw: bool = False, raw_dir: Optional[Path] = None, **kwargs) -> ATUpdateFetchResult:
        del run_external, kwargs
        if _text(source).casefold() not in {"all", "at", "austria", "ages", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported AT source: {source!r}")
        today = datetime.now(timezone.utc).date()
        targets = list(months) if months is not None else self._recent_months(today, self.refresh_recent_months)
        crawler = self.crawler_type(save_raw=save_raw, raw_dir=raw_dir or ROOT / "data/raw/at")
        summary = crawler.crawl_monthly_national(self.output_csv, months=targets, backfill_history=force)
        rows = self._load_rows(self.output_csv)
        if not force:
            wanted = set(targets)
            rows = [row for row in rows if (parsed := _parse_date(row)) and (parsed.year, parsed.month) in wanted]
        return ATUpdateFetchResult(rows, summary.latest_date, self.output_csv, [f"[crawler] prepared {summary.row_count} AGES source-native rows across {summary.months_fetched} issue(s)", "[gate] public release disabled pending AGES license review"])

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code"), {"code": self.country_code})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db: AsyncSession) -> Set[tuple[int, int]]:
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code"), {"code": self.country_code})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    async def import_rows(self, db: AsyncSession, rows: List[Dict[str, str]], *, db_latest_date: Optional[date], source_latest_date: Optional[date], force: bool = False) -> ATUpdateImportResult:
        # CrawlService persists every row to DiseaseSeriesObservation and then
        # reports that upsert count through this result.  Canonical projections
        # are resolved from the active Mapping Registry v3 release; this updater
        # intentionally performs no direct write to the legacy disease table.
        del db, rows, force
        return ATUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)


__all__ = ["ATMonthlyUpdater", "ATUpdateFetchResult", "ATUpdateImportResult", "DEFAULT_OUTPUT_CSV"]
