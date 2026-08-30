"""Singapore CDA weekly source refresh and source-series import contract."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.sg import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    HISTORICAL_ONTOLOGY_SOURCE_ID,
    HISTORICAL_SOURCE_NAME,
    HISTORY_START_YEAR,
    ONTOLOGY_SOURCE_ID,
    SingaporeCDACrawler,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/sg/singapore_cda_weekly.csv"


@dataclass(frozen=True)
class SGUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass(frozen=True)
class SGUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def _date(row: Dict[str, str]) -> Optional[date]:
    try:
        return date.fromisoformat(_text(row.get("Date")))
    except ValueError:
        return None


class SGWeeklyUpdater:
    country_code = "SG"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = {
        HISTORICAL_SOURCE_NAME: HISTORICAL_ONTOLOGY_SOURCE_ID,
        DEFAULT_SOURCE_NAME: ONTOLOGY_SOURCE_ID,
    }
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_geography_key = "country:SG:national"
    public_release_enabled = True
    license_review_status = "operator_authorized_public_release"

    def __init__(self, *, output_csv: Path = DEFAULT_OUTPUT_CSV,
                 refresh_recent_weeks: int = 12,
                 crawler_type=SingaporeCDACrawler) -> None:
        self.output_csv = Path(output_csv)
        self.refresh_recent_weeks = max(4, min(26, int(refresh_recent_weeks)))
        self.full_history_start_year = HISTORY_START_YEAR
        self.crawler_type = crawler_type

    def _load_rows(self) -> List[Dict[str, str]]:
        if not self.output_csv.exists():
            return []
        with self.output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [
                {str(key): _text(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
                if _date(row) is not None and _text(row.get("Cases")) != ""
            ]
        return sorted(rows, key=lambda row: (row["Date"], row.get("SourceDiseaseCode", "")))

    def refresh_source(self, *, source: str = DEFAULT_SCOPE, run_external: bool = False,
                       force: bool = False, fill_missing: bool = False,
                       save_raw: bool = False, raw_dir: Optional[Path] = None,
                       start_year: Optional[int] = None, **kwargs) -> SGUpdateFetchResult:
        del run_external, kwargs
        if _text(source).casefold() not in {
            "all", "sg", "singapore", "cda", "widb", DEFAULT_SCOPE,
        }:
            raise ValueError(f"Unsupported SG source: {source!r}")
        current_year = datetime.now(timezone.utc).year
        first_year = max(HISTORY_START_YEAR, min(current_year, int(start_year or HISTORY_START_YEAR)))
        full = bool(force or fill_missing)
        years = list(range(first_year, current_year + 1)) if full else sorted({max(2024, current_year - 1), current_year})
        crawler = self.crawler_type(save_raw=save_raw, raw_dir=raw_dir or ROOT / "data/raw/sg")
        summary = crawler.crawl_weekly_national(self.output_csv, years=years, include_history=full)
        rows = self._load_rows()
        if full:
            rows = [row for row in rows if int(row.get("Year") or 0) >= first_year]
        if not full:
            recent_dates = sorted({_date(row) for row in rows if _date(row) is not None}, reverse=True)[:self.refresh_recent_weeks]
            rows = [row for row in rows if _date(row) in set(recent_dates)]
        return SGUpdateFetchResult(
            rows=rows,
            source_latest_date=summary.latest_date,
            source_csv=self.output_csv,
            script_logs=[
                f"[crawler] prepared {summary.row_count} CDA source-native rows from {summary.artifacts_fetched} official artifact(s)",
                f"[planner] CDA years fetched: {', '.join(map(str, summary.years_fetched))}",
                "[source] 2012-2022 data.gov.sg CSV; 2023+ CDA publications",
                "[gate] public release enabled by explicit operator authorization; CDA terms status retained",
            ],
        )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code"
        ), {"code": self.country_code})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_week_dates(self, db: AsyncSession) -> Set[date]:
        rows = (await db.execute(text(
            "SELECT DISTINCT DATE(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code"
        ), {"code": self.country_code})).fetchall()
        return {row[0] for row in rows if row[0] is not None}

    async def import_rows(self, db: AsyncSession, rows: List[Dict[str, str]], *,
                          db_latest_date: Optional[date], source_latest_date: Optional[date],
                          force: bool = False) -> SGUpdateImportResult:
        # Preserve all CDA categories in the source-series store.  Direct legacy
        # projection is intentionally omitted because Dengue and DHF are
        # separate CDA series but share a canonical disease concept.
        del db, rows, force
        return SGUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)


__all__ = ["DEFAULT_OUTPUT_CSV", "SGUpdateFetchResult", "SGUpdateImportResult", "SGWeeklyUpdater"]
