"""Indonesia Ministry of Health source-native annual facts; Registry v3 owns database writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.id import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID


@dataclass(frozen=True)
class IndonesiaFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class IndonesiaImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class IndonesiaKemkesProfileUpdater:
    country_code = "ID"
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:ID:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "unverified"

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        self.output_csv = Path(output_csv or Path(__file__).resolve().parents[3] / "data/current/id/indonesia_kemkes_profile.csv")
        self.full_history_start_year = int(full_history_start_year)

    @staticmethod
    def normalize_source(source):
        if str(source or "all").lower() not in {"all", "id", "indonesia", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported Indonesia source: {source!r}")

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return IndonesiaImportResult(0, 0, db_latest_date, source_latest_date, False)
