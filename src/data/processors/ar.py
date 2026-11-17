"""BEN source-native cumulative snapshots; shared v3 store owns DB writes."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from sqlalchemy import text
from src.data.crawlers.ar import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, ONTOLOGY_SOURCE_ID, HISTORY_START_YEAR, ArgentinaBENCrawler, deduplicate_revisions

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/ar/argentina_ben_weekly.csv"


@dataclass(frozen=True)
class ArgentinaBENFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArgentinaBENImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class ArgentinaBENUpdater:
    country_code = "AR"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:AR:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "source_reports_reuse_review_pending"
    source_aliases = {"all", "ar", "argentina", "ben", DEFAULT_SCOPE}

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, refresh_recent_months=3, crawler_type=None):
        self.output_csv = Path(output_csv or DEFAULT_OUTPUT_CSV)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)
        self.crawler_type = crawler_type or ArgentinaBENCrawler

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported AR source {source!r}")

    @staticmethod
    def parse_source_csv(payload, **kwargs):
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, source_audit=None, **options):
        self.normalize_source(source)
        first = self.full_history_start_year if start_year is None else int(start_year)
        rows = deduplicate_revisions([row for row in rows if date.fromisoformat(row["Date"]).year >= first])
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        audit = source_audit or {}
        if not rows:
            raise ValueError("No validated AR ENO observations; inspect source audit")
        return ArgentinaBENFetchResult(rows, latest, self.output_csv, [f"[source] AR BEN: {len(rows)} source-native cumulative ENO observations", "[coverage] Cumulative snapshots, never weekly incident counts; archive audit records unparsed reports and unsupported tables", "[release] public publication pending validated snapshot and source terms"], audit)

    def refresh_source(self, *, source=DEFAULT_SCOPE, start_year=None, force=False, **options):
        self.normalize_source(source)
        crawler = self.crawler_type()
        try:
            rows = crawler.fetch_rows(start_year=start_year or self.full_history_start_year, force=force)
            return self.prepare_rows(rows, source=source, start_year=start_year, source_audit=crawler.last_audit)
        finally:
            crawler.session.close()

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return ArgentinaBENImportResult(0, 0, db_latest_date, source_latest_date, False)
