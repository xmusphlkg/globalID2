"""DDS weekly source-native facts; no legacy projection or notification emails."""
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.th_dds import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, ONTOLOGY_SOURCE_ID


@dataclass(frozen=True)
class DDSFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class DDSImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class ThailandDDSWeeklyUpdater:
    country_code = "TH"
    series_country_code = "TH"
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:TH:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_quality_guard = {"mode": "fail_closed", "history_lookback_days": 1095}
    mapping_notifications_enabled = False
    public_release_enabled = False
    refresh_recent_months = 3

    def __init__(self, *, output_csv=None):
        root = Path(__file__).resolve().parents[3]
        self.output_csv = Path(output_csv or root / "data/current/th/thailand_dds_weekly.csv")

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return DDSImportResult(0, 0, db_latest_date, source_latest_date, False)
