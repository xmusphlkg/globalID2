"""Bhutan RCDC source-native updater; documents remain distinct from cases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.bt import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class BhutanRCDCFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class BhutanRCDCImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class BhutanRCDCPublicationsUpdater:
    requires_verified_observations = True
    country_code = "BT"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:BT:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "reuse_terms_unverified"
    source_aliases = {"all", "bt", "bhutan", DEFAULT_SCOPE}
    default_output_csv = ROOT / "data/current/bt/rcdc_sentinel_weekly.csv"

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR,
                 refresh_recent_months=3, **kwargs):
        self.output_csv = Path(output_csv or self.default_output_csv)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported BT source: {source!r}")

    async def get_db_latest_date(self, db):
        result = await db.execute(text("""
            SELECT MAX(obs.time) FROM disease_series_observations obs
            JOIN disease_surveillance_series series ON series.series_code=obs.series_code
            WHERE series.country_code=:code AND series.source_system=:source_id
        """), {"code": self.country_code, "source_id": self.ontology_source_id})
        value = result.scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        result = await db.execute(text("""
            SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int
            FROM disease_series_observations obs
            JOIN disease_surveillance_series series ON series.series_code=obs.series_code
            WHERE series.country_code=:code AND series.source_system=:source_id
        """), {"code": self.country_code, "source_id": self.ontology_source_id})
        return {(int(row[0]), int(row[1])) for row in result.fetchall()}

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, end_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        start_year = self.full_history_start_year if start_year is None else int(start_year)
        rows = [row for row in rows if start_year <= int(row["SourceReportYear"]) <= int(end_year or 9999)]
        audit = dict(source_audit or {})
        audit.update(observations=len(rows), coverage_complete=False)
        return BhutanRCDCFetchResult(rows, max((date.fromisoformat(row["Date"]) for row in rows), default=None),
                                    self.output_csv, [f"[source] {DEFAULT_SOURCE_NAME}: {len(rows)} verified weekly laboratory detections",
                                                      "[coverage] publication archive collected; unparsed documents and missing weeks remain unknown"], audit)

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return BhutanRCDCImportResult(0, 0, db_latest_date, source_latest_date, False)
