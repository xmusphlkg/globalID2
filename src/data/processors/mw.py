"""Source-native Malawi PHIM IDSR weekly observations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.mw import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, ONTOLOGY_SOURCE_ID

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/mw/malawi_phim_idsr_weekly.csv"


@dataclass(frozen=True)
class MalawiIDSRFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class MalawiIDSRImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class MalawiPHIMIDSRUpdater:
    country_code = "MW"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:MW:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "reuse_terms_unverified"
    source_aliases = {"all", "mw", "malawi", DEFAULT_SCOPE}
    default_output_csv = DEFAULT_OUTPUT_CSV

    def __init__(self, *, output_csv=None, full_history_start_year=2025,
                 refresh_recent_months=3, **kwargs):
        self.output_csv = Path(output_csv or self.default_output_csv)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported MW source: {source!r}")

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

    def prepare_rows(self, rows, *, source="all", start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        year = self.full_history_start_year if start_year is None else int(start_year)
        selected = [row for row in rows if int(row.get("SourceReportYear", row["Date"][:4])) >= year]
        if not selected:
            raise ValueError("No verified PHIM IDSR observations; inspect source audit")
        latest = max(date.fromisoformat(row["Date"]) for row in selected)
        audit = dict(source_audit or {})
        audit["observations"] = len(selected)
        return MalawiIDSRFetchResult(selected, latest, self.output_csv, [
            f"[source] {DEFAULT_SOURCE_NAME}: {len(selected)} verified source observations",
            f"[coverage] {audit.get('status', 'partial')}; discovered reports and parsed tables are separate",
            "[release] public release pending source review and verified snapshot",
        ], audit)

    async def import_rows(self, db, rows, *, db_latest_date=None,
                          source_latest_date=None, force=False):
        # The shared source-series store handles all writes.
        return MalawiIDSRImportResult(0, 0, db_latest_date, source_latest_date, False)
