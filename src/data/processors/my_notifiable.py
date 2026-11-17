"""All published MOH annual STD categories; v3 owns observation writes."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.my_notifiable import DEFAULT_SCOPE, ONTOLOGY_SOURCE_ID, MalaysiaMOHNotifiableCrawler

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/my/malaysia_moh_std_annual.csv"


@dataclass(frozen=True)
class MYAnnualFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]


@dataclass(frozen=True)
class MYAnnualImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class MYAnnualUpdater:
    country_code = "MY"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:MY:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "cc_by_4_0"
    source_aliases = {"all", "my", "malaysia", DEFAULT_SCOPE}
    default_output_csv = DEFAULT_OUTPUT_CSV
    default_crawler_type = MalaysiaMOHNotifiableCrawler
    observation_description = "source-native annual STD notification observations (all published std_state categories)"

    def __init__(self, *, output_csv=None, full_history_start_year=2017, refresh_recent_months=3, crawler_type=None):
        self.output_csv = Path(output_csv or self.default_output_csv)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)
        self.crawler_type = crawler_type or self.default_crawler_type

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported {cls.country_code} source: {source!r}")

    @staticmethod
    def parse_source_csv(payload, **kwargs):
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    def prepare_rows(self, rows, *, source="all", start_year=None, months=None, **kwargs):
        self.normalize_source(source)
        start_year = self.full_history_start_year if start_year is None else int(start_year)
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        if latest and int(start_year) > latest.year:
            raise ValueError(f"Requested start year is after {self.country_code} source coverage ({latest})")
        # Annual January stamps must not be filtered by monthly planning windows.
        selected = [row for row in rows if date.fromisoformat(row["Date"]).year >= int(start_year)]
        return MYAnnualFetchResult(selected, latest, self.output_csv, [f"[source] {self.country_code}: {len(selected)} {self.observation_description}; latest available {latest}", "[coverage] This published dataset covers STDs, not all Malaysian notifiable diseases", "[release] pending verified country snapshot"])

    def refresh_source(self, *, source="all", start_year=None, months=None, **kwargs):
        self.normalize_source(source)
        crawler = self.crawler_type()
        try:
            return self.prepare_rows(crawler.fetch_rows(), source=source, start_year=start_year, months=months)
        finally:
            crawler.session.close()

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return MYAnnualImportResult(0, 0, db_latest_date, source_latest_date, False)
