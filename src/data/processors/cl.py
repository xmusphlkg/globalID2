"""Chile ENO source-series updater; raw case records become annual counts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.cl import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/cl/chile_eno_historical.csv"


@dataclass(frozen=True)
class ChileENOFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class ChileENOImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class ChileENOHistoricalUpdater:
    country_code = "CL"
    full_history_start_year = HISTORY_START_YEAR
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_country_code = "CL"
    series_geography_from_rows = True
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    source_aliases = {"all", "cl", "chile", DEFAULT_SCOPE}
    default_output_csv = DEFAULT_OUTPUT_CSV

    def __init__(self, *, output_csv=None, **kwargs):
        self.output_csv = Path(output_csv or self.default_output_csv)

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported CL source: {source!r}")

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, source_audit=None, **kwargs):
        self.normalize_source(source)
        selected = list(rows)
        if not selected:
            raise ValueError("No validated Chile ENO observations")
        audit = dict(source_audit or {})
        return ChileENOFetchResult(selected, max(date.fromisoformat(row["Date"]) for row in selected),
                                   self.output_csv, [f"[CL] {len(selected)} annual source-native ENO counts",
                                                     "[CL] Public release awaits source contract and snapshot review"], audit)

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return ChileENOImportResult(0, 0, db_latest_date, source_latest_date, False)
