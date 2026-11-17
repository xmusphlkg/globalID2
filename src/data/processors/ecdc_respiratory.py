"""ECDC laboratory source updater; no national patient case projection."""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import csv
import io
from sqlalchemy import text
from src.data.crawlers.ecdc_respiratory import COUNTRY_NAMES, DEFAULT_SCOPE, ECDCRespiratoryCrawler


@dataclass(frozen=True)
class EuropeanFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EuropeanImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class ECDCRespiratoryUpdater:
    source_scope = DEFAULT_SCOPE
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_quality_guard = {"mode": "fail_closed"}
    public_release_enabled = True
    mapping_notifications_enabled = False
    license_review_status = "operator_authorized_official_source_release"

    def __init__(self, *, country_code, output_csv=None, full_history_start_year=2022, refresh_recent_months=3, crawler_type=None):
        self.country_code = str(country_code).upper()
        if self.country_code not in COUNTRY_NAMES:
            raise ValueError("Unsupported ECDC respiratory country")
        self.ontology_source_id = f"SRC_{self.country_code}_ECDC_RESPIRATORY"
        self.series_geography_key = f"country:{self.country_code}:national"
        self.output_csv = Path(output_csv or f"data/current/{self.country_code.lower()}/ecdc_respiratory_weekly.csv")
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)
        self.crawler_type = crawler_type or ECDCRespiratoryCrawler

    def normalize_source(self, source):
        if str(source or self.source_scope).casefold() not in {self.source_scope, "all"}:
            raise ValueError(f"Unsupported European source: {source!r}")

    @staticmethod
    def parse_source_csv(payload, **kwargs):
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, end_year=None, months=None, source_audit=None, **options):
        self.normalize_source(source)
        first = self.full_history_start_year if start_year is None else int(start_year)
        selected = [r for r in rows if int(r.get("SourceYear", r["Year"])) >= first and (end_year is None or int(r.get("SourceYear", r["Year"])) <= int(end_year))]
        latest = max((date.fromisoformat(r["Date"]) for r in selected), default=None)
        return EuropeanFetchResult(selected, latest, self.output_csv,
            [f"[source] {self.country_code} {self.source_scope}: {len(selected)} source-native observations; latest {latest}",
             "[coverage] Source periods, measures and overlapping strata are retained independently; no invented observations"], source_audit or {})

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(o.time) FROM disease_series_observations o JOIN disease_surveillance_series s USING(series_code) WHERE s.country_code=:country AND s.source_system=:source"), {"country": self.country_code, "source": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM o.time)::int, EXTRACT(MONTH FROM o.time)::int FROM disease_series_observations o JOIN disease_surveillance_series s USING(series_code) WHERE s.country_code=:country AND s.source_system=:source"), {"country": self.country_code, "source": self.ontology_source_id})).fetchall()
        return {(int(y), int(m)) for y, m in rows}

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return EuropeanImportResult(0, 0, db_latest_date, source_latest_date, False)
