"""Lossless annual RIVM source rows; disease interpretation stays in Registry v3."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.nl_notifiable import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID, NetherlandsNotifiableCrawler,
)

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class NLNotifiableFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]


@dataclass(frozen=True)
class NLNotifiableImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class NLNotifiableAnnualUpdater:
    country_code = "NL"
    series_country_code = "NL"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_from_rows = True
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_quality_guard = {"mode": "fail_closed", "history_lookback_days": 7300}
    public_release_enabled = False
    mapping_notifications_enabled = False
    full_history_start_year = HISTORY_START_YEAR

    def __init__(self, *, output_csv=None, crawler_type=NetherlandsNotifiableCrawler):
        self.output_csv = Path(output_csv or ROOT / "data/current/nl/netherlands_rivm_notifiable_annual.csv")
        self.crawler_type = crawler_type

    @staticmethod
    def normalize_source(source):
        if str(source or "all").strip().casefold() not in {"all", "nl", "netherlands", "rivm_notifiable", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported Netherlands annual notification source: {source!r}")

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, **options):
        self.normalize_source(source)
        if not rows:
            raise ValueError("The annual notification source produced no eligible rows")
        latest = max(date.fromisoformat(row["Date"]) for row in rows)
        return NLNotifiableFetchResult(rows, latest, self.output_csv, [
            f"[source] {len(rows)} annual observations across {len({row['SourceDiseaseCode'] for row in rows})} RIVM categories",
            "[scope] Caribbean Netherlands and outbreak counts remain separate source identities; no legacy projection",
        ])

    def refresh_source(self, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, **options):
        self.normalize_source(source)
        crawler = self.crawler_type()
        try:
            return self.prepare_rows(crawler.fetch_rows(start_year=start_year), source=source)
        finally:
            crawler.session.close()

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return NLNotifiableImportResult(0, 0, db_latest_date, source_latest_date, False)
