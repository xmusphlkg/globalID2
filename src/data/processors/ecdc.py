"""ECDC annual baseline refresh contract."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.ecdc import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    ECDCAtlasCrawler,
    ONTOLOGY_SOURCE_ID_TEMPLATE,
)

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ECDCUpdateFetchResult:
    rows: List[Dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: List[str]


@dataclass(frozen=True)
class ECDCUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split())


class ECDCAnnualUpdater:
    source_scope = DEFAULT_SCOPE
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = True
    license_review_status = "ecdc_publication_and_reproduction_authorized_with_attribution"

    def __init__(self, country_code: str = "FR", *, output_csv: Optional[Path] = None, full_history_start_year: int = 1990, crawler_type=ECDCAtlasCrawler) -> None:
        self.country_code = _text(country_code).upper()
        self.output_csv = Path(output_csv or ROOT / f"data/current/{self.country_code.casefold()}/{self.country_code.casefold()}_ecdc_atlas_annual.csv")
        self.full_history_start_year = int(full_history_start_year)
        self.crawler_type = crawler_type
        self.ontology_source_id = ONTOLOGY_SOURCE_ID_TEMPLATE.format(country=self.country_code)
        self.series_geography_key = f"country:{self.country_code}:national"

    def _load_rows(self) -> List[Dict[str, str]]:
        if not self.output_csv.exists():
            return []
        with self.output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {str(key): _text(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
                if _text(row.get("Date")) and _text(row.get("Cases"))
            ]

    def refresh_source(self, *, source: str = DEFAULT_SCOPE, force: bool = False, fill_missing: bool = False, save_raw: bool = False, raw_dir: Optional[Path] = None, start_year: Optional[int] = None, **kwargs) -> ECDCUpdateFetchResult:
        del force, fill_missing, kwargs
        if _text(source).casefold() not in {"all", "ecdc", "atlas", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported ECDC source: {source!r}")
        first_year = max(self.full_history_start_year, int(start_year or self.full_history_start_year))
        crawler = self.crawler_type(self.country_code, save_raw=save_raw, raw_dir=raw_dir)
        summary = crawler.crawl_annual_baseline(self.output_csv, start_year=first_year)
        return ECDCUpdateFetchResult(
            self._load_rows(), summary.latest_date, self.output_csv,
            [
                f"[crawler] prepared {summary.row_count} annual ECDC observations across {summary.series_count} source series",
                f"[coverage] {summary.first_date or 'none'} through {summary.latest_date or 'none'}",
                "[license] publication and reproduction authorized with ECDC and Member State attribution",
            ],
        )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def delete_authoritative_window(
        self, db: AsyncSession, *, start_year: int
    ) -> int:
        """Delete only this source's fetched window before atomic reimport.

        ECDC may withdraw a previously published cell. Replacing the fetched
        window in the same transaction ensures that withdrawal becomes unknown
        in the database rather than leaving a stale observation behind.
        """
        result = await db.execute(text(
            "DELETE FROM disease_series_observations obs "
            "USING disease_surveillance_series series "
            "WHERE obs.series_code=series.series_code "
            "AND series.country_code=:code "
            "AND series.source_system=:source_id "
            "AND obs.time >= :window_start"
        ), {
            "code": self.country_code,
            "source_id": self.ontology_source_id,
            "window_start": date(int(start_year), 1, 1),
        })
        return int(result.rowcount or 0)

    async def import_rows(self, db: AsyncSession, rows: List[Dict[str, str]], *, db_latest_date: Optional[date], source_latest_date: Optional[date], force: bool = False) -> ECDCUpdateImportResult:
        del db, rows, force
        return ECDCUpdateImportResult(0, 0, db_latest_date, source_latest_date, False)


__all__ = ["ECDCAnnualUpdater", "ECDCUpdateFetchResult", "ECDCUpdateImportResult"]
