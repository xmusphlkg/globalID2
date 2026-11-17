"""Internal Pakistan NIH IDSR source-series holding; no legacy projection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.pk_idsr import (
    DEFAULT_SCOPE, DEFAULT_SOURCE_NAME,
    HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID,
)


@dataclass(frozen=True)
class PakistanIDSRFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class PakistanIDSRImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class PakistanNIHIDSRUpdater:
    country_code = "PK"
    series_country_code = "PK"
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = None
    series_geography_from_rows = True
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "reuse_terms_unverified"
    source_aliases = {"all", "pk", "pakistan", DEFAULT_SCOPE}

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR,
                 refresh_recent_months=3, **kwargs):
        self.output_csv = Path(output_csv or Path(__file__).resolve().parents[3] / "data/current/pk/pakistan_nih_idsr_weekly.csv")
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported Pakistan IDSR source: {source!r}")

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, source_audit=None, **options):
        self.normalize_source(source)
        first = self.full_history_start_year if start_year is None else int(start_year)
        selected = [row for row in rows if int(row["SourcePeriodYear"]) >= first]
        if not selected:
            raise ValueError("No validated NIH IDSR observations; inspect captured collection audit")
        audit = dict(source_audit or {})
        latest = max((int(row["SourcePeriodYear"]), int(row["SourcePeriodWeek"])) for row in selected)
        audit.update(latest_source_year=latest[0], latest_source_week=latest[1],
                     observations=len(selected),
                     numeric_observations=sum(bool(row.get("Cases", "")) for row in selected),
                     source_not_reported_observations=sum(row.get("MissingValueStatus") == "source_not_reported" for row in selected),
                     authoritative_replacement=False,
                     public_release_enabled=False, calendar_verified=False,
                     storage_date_role="source_year_anchor_only", dates_inferred=False)
        return PakistanIDSRFetchResult(selected, date(latest[0], 1, 1), self.output_csv,
            [f"[PK] {len(selected)} suspected-case source observations; latest source week {latest[0]} W{latest[1]:02d}",
             "[calendar] January 1 is a storage anchor only; source weeks are not verified calendar dates",
             "[coverage] Published totals cover reporting areas; NR is unknown; laboratory data remain separate",
             "[release] Internal holding only; public release disabled"], audit)

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text(
            "SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int "
            "FROM disease_series_observations obs JOIN disease_surveillance_series series "
            "ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        # The shared source-series store owns transactional writes.
        return PakistanIDSRImportResult(0, 0, db_latest_date, source_latest_date, False)
