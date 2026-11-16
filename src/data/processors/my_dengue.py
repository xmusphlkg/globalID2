"""Source-native iDengue daily observations; series store owns fact writes."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

from src.data.crawlers.my_dengue import (
    DEFAULT_SCOPE,
    ONTOLOGY_SOURCE_ID,
    MalaysiaIDengueCrawler,
)
from src.data.processors.my_notifiable import MYAnnualImportResult, MYAnnualUpdater

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class IDengueFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


class MalaysiaIDengueDailyUpdater(MYAnnualUpdater):
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    # Geography comes from each explicit row, never a forced national key.
    series_geography_key = None
    series_geography_from_rows = True
    series_quality_guard: ClassVar[dict[str, object]] = {"mode": "fail_closed", "history_lookback_days": 1095}
    source_aliases = frozenset({DEFAULT_SCOPE, "idengue", "my_idengue"})
    default_output_csv = ROOT / "data/current/my/idengue_daily.csv"
    default_crawler_type = MalaysiaIDengueCrawler
    license_review_status = "pending_source_reuse_review"
    observation_description = "explicit daily dengue counts for Malaysia and source reporting regions"

    def __init__(self, *, output_csv=None, full_history_start_year=None, refresh_recent_months=3, crawler_type=None):
        # This is only a planner bound, never a claim of historical coverage.
        super().__init__(output_csv=output_csv, full_history_start_year=full_history_start_year or datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).year,
                         refresh_recent_months=refresh_recent_months, crawler_type=crawler_type)

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, months=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        if start_year is not None and latest and int(start_year) > latest.year:
            raise ValueError("Requested start year is after the captured iDengue daily date")
        # The portal exposes its current published day. A historical scheduler
        # month window must not silently discard this snapshot.
        selected = [row for row in rows if start_year is None or date.fromisoformat(row["Date"]).year >= int(start_year)]
        return IDengueFetchResult(selected, latest, self.output_csv,
            [f"[source] iDengue {len(selected)} explicit daily observations for {latest}",
             "[coverage] Current daily snapshot; historical daily backfill not verified",
             "[semantics] Cumulative cases and deaths are not daily observations"], source_audit or {})

    @staticmethod
    def parse_source_csv(payload, **kwargs):
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        return MYAnnualImportResult(0, 0, db_latest_date, source_latest_date, False)
