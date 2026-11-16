"""RIVM weekly native facts, retaining source periods and metric contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

from sqlalchemy import text

from src.data.crawlers.nl_weekly import (
    RESP_SCOPE,
    RESP_SOURCE_ID,
    VIRO_SCOPE,
    VIRO_SOURCE_ID,
)

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class NLWeeklyFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NLWeeklyImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class _NLWeeklyUpdater:
    country_code = "NL"
    series_country_code = "NL"
    series_geography_key = "country:NL:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    series_quality_guard: ClassVar[dict] = {"mode": "fail_closed", "history_lookback_days": 7300}
    public_release_enabled = False
    mapping_notifications_enabled = False
    full_history_start_year = 2000

    def __init__(self, *, output_csv=None, **kwargs):
        self.output_csv = Path(output_csv or ROOT / f"data/current/nl/{self.source_scope}.csv")

    @classmethod
    def normalize_source(cls, source):
        if str(source or cls.source_scope).casefold() != cls.source_scope:
            raise ValueError(f"Unsupported RIVM weekly source: {source!r}")

    def prepare_rows(self, rows, *, source, source_audit=None, **kwargs):
        self.normalize_source(source)
        if not rows:
            raise ValueError("RIVM weekly source produced no observations")
        # Do not apply monthly date filtering to year storage anchors.
        latest = max(date.fromisoformat(r["Date"]) for r in rows)
        return NLWeeklyFetchResult(rows, latest, self.output_csv,
            [f"[source] {len(rows)} source-native weekly observations",
             "[calendar] Source year/week or season/week retained; January 1 timestamps are storage anchors, not weekly dates"],
            source_audit or {})

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(o.time) FROM disease_series_observations o "
            "JOIN disease_surveillance_series s USING(series_code) "
            "WHERE s.country_code=:country AND s.source_system=:source"
        ), {"country": self.country_code, "source": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return NLWeeklyImportResult(0, 0, db_latest_date, source_latest_date, False)


class NLVirologicalWeeklyUpdater(_NLWeeklyUpdater):
    source_scope = VIRO_SCOPE
    ontology_source_id = VIRO_SOURCE_ID


class NLRespiratoryWeeklyUpdater(_NLWeeklyUpdater):
    source_scope = RESP_SCOPE
    ontology_source_id = RESP_SOURCE_ID
