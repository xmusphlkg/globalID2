"""Direct annual national HIV notifications; staged v3 ingestion owns writes."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import io
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.ar_hiv import (
    ArgentinaHIVAnnualCrawler, DEFAULT_SCOPE, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/ar/hiv_annual/argentina_hiv_annual.csv"


@dataclass(frozen=True)
class ArgentinaHIVAnnualFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArgentinaHIVAnnualImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class ArgentinaHIVAnnualUpdater:
    country_code = "AR"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    full_history_start_year = HISTORY_START_YEAR
    series_geography_key = "country:AR:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "cc_by_4_0_resource_metadata"
    source_aliases = {DEFAULT_SCOPE, "hiv", "ar_hiv"}

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR,
                 refresh_recent_months=3, crawler_type=None):
        self.output_csv = Path(output_csv or DEFAULT_OUTPUT_CSV)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)
        self.crawler_type = crawler_type or ArgentinaHIVAnnualCrawler

    @classmethod
    def normalize_source(cls, source):
        if str(source or DEFAULT_SCOPE).strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported AR annual HIV source: {source!r}")

    @staticmethod
    def parse_source_csv(payload, **kwargs):
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, months=None,
                     source_audit=None, **options):
        self.normalize_source(source)
        first = self.full_history_start_year if start_year is None else int(start_year)
        selected = [row for row in rows if date.fromisoformat(row["Date"]).year >= first]
        if not selected:
            raise ValueError("No source-reported national annual HIV observations in requested years")
        latest = max(date.fromisoformat(row["Date"]) for row in selected)
        return ArgentinaHIVAnnualFetchResult(selected, latest, self.output_csv,
            [f"[source] AR HIV: {len(selected)} direct national annual notifications; latest {latest.year}",
             "[coverage] Reviewed release covers 2014–2023 only; annual notification counts, not incidence or prevalence",
             "[selection] Explicit ARG/200 and Ambos sexos; no province/sex summation or cumulative differencing"],
            dict(source_audit or {}))

    def refresh_source(self, *, source=DEFAULT_SCOPE, start_year=None, **options):
        self.normalize_source(source)
        crawler = self.crawler_type()
        try:
            return self.prepare_rows(crawler.fetch_rows(), source=source, start_year=start_year,
                                     source_audit=crawler.last_audit)
        finally:
            crawler.session.close()

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"),
                                  {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"),
                                 {"code": self.country_code, "source_id": self.ontology_source_id})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return ArgentinaHIVAnnualImportResult(0, 0, db_latest_date, source_latest_date, False)
