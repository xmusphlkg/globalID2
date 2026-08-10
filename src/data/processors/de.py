"""Germany RKI SurvStat updater with lossless source-series retention."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.de import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID, GermanySurvStatCrawler

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/de/germany_rki_survstat_weekly.csv"
MAPPING_SOURCE_ID = ONTOLOGY_SOURCE_ID


@dataclass(frozen=True)
class DEUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass(frozen=True)
class DEUpdateImportResult:
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


class DEWeeklyUpdater:
    country_code = "DE"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_geography_key = "country:DE:national"
    public_release_enabled = True
    license_review_status = "reviewed_source_attribution_required"

    def __init__(self, *, output_csv: Path = DEFAULT_OUTPUT_CSV, full_history_start_year: int = HISTORY_START_YEAR, refresh_recent_weeks: int = 12, crawler_type=GermanySurvStatCrawler, export_url_template: Optional[str] = None) -> None:
        self.output_csv = Path(output_csv)
        self.full_history_start_year = max(HISTORY_START_YEAR, int(full_history_start_year))
        self.refresh_recent_weeks = max(4, min(52, int(refresh_recent_weeks)))
        self.crawler_type = crawler_type
        self.export_url_template = export_url_template if export_url_template is not None else os.getenv("DE_SURVSTAT_EXPORT_URL_TEMPLATE", "")

    @staticmethod
    def _last_closed_week(today: date) -> date:
        return today - timedelta(days=today.weekday() + 7)

    def history_weeks(self, *, today: Optional[date] = None, start_year: Optional[int] = None) -> List[date]:
        end = self._last_closed_week(today or datetime.now(timezone.utc).date())
        first_year = max(self.full_history_start_year, int(start_year or self.full_history_start_year))
        start = date(first_year, 1, 1)
        start -= timedelta(days=start.weekday())
        result: List[date] = []
        while start <= end:
            result.append(start); start += timedelta(days=7)
        return result

    def _recent_weeks(self, today: date) -> List[date]:
        end = self._last_closed_week(today)
        return [end - timedelta(days=7 * delta) for delta in range(self.refresh_recent_weeks)]

    def _load_rows(self, path: Path) -> List[Dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [{key: _text(value) for key, value in row.items()} for row in csv.DictReader(handle) if _parse_date(row) and _parse_cases(row) is not None]

    def refresh_source(self, *, source: str = DEFAULT_SCOPE, run_external: bool = False, force: bool = False, weeks: Optional[Sequence[date]] = None, save_raw: bool = False, raw_dir: Optional[Path] = None, **kwargs) -> DEUpdateFetchResult:
        del run_external, kwargs
        if _text(source).casefold() not in {"all", "de", "germany", "rki", "survstat", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported DE source: {source!r}")
        today = datetime.now(timezone.utc).date()
        targets = list(weeks) if weeks is not None else (self.history_weeks(today=today) if force else self._recent_weeks(today))
        years = sorted({target.isocalendar().year for target in targets})
        crawler = self.crawler_type(save_raw=save_raw, raw_dir=raw_dir or ROOT / "data/raw/de", export_url_template=self.export_url_template)
        summary = crawler.crawl_weekly_national(self.output_csv, years=years)
        rows = self._load_rows(self.output_csv)
        wanted = set(targets)
        if not force:
            rows = [row for row in rows if _parse_date(row) in wanted]
        return DEUpdateFetchResult(rows, summary.latest_date, self.output_csv, [f"[crawler] prepared {summary.row_count} RKI source-native rows for {summary.years_fetched} year export(s)", f"[planner] target weeks={len(targets)}; refresh window={self.refresh_recent_weeks}"])

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code"), {"code": self.country_code})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_week_dates(self, db: AsyncSession) -> Set[date]:
        rows = (await db.execute(text("SELECT DISTINCT DATE(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code"), {"code": self.country_code})).fetchall()
        return {row[0] for row in rows if row[0] is not None}

    async def import_rows(self, db: AsyncSession, rows: List[Dict[str, str]], *, db_latest_date: Optional[date], source_latest_date: Optional[date], force: bool = False) -> DEUpdateImportResult:
        # The shared source-series store is the only fact writer.  Mapping
        # Registry v3 projects reviewed concepts from an active release, so an
        # unknown German category remains visible and reviewable without ever
        # entering the legacy disease table under a guessed identifier.
        del db, rows, force
        return DEUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)


__all__ = ["DEWeeklyUpdater", "DEUpdateFetchResult", "DEUpdateImportResult", "DEFAULT_OUTPUT_CSV"]
