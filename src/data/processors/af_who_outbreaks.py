"""WHO Afghanistan current-week outbreak tables, with explicit coverage gaps."""
from datetime import date
from pathlib import Path

from src.data.crawlers.af_who_outbreaks import (
    DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID,
    GEOGRAPHY_KEY, AfghanistanWHOOutbreaksCrawler,
)
from src.data.processors.my_who_annual import MYWHOAnnualUpdater, MYWHOAnnualFetchResult


class AfghanistanWHOOutbreaksUpdater(MYWHOAnnualUpdater):
    country_code = "AF"
    source_scope = DEFAULT_SCOPE
    requires_verified_observations = True
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = GEOGRAPHY_KEY
    source_aliases = {"all", "af", "afghanistan", DEFAULT_SCOPE}
    default_output_csv = Path("data/current/af/afghanistan_who_outbreaks_weekly.csv")
    default_crawler_type = AfghanistanWHOOutbreaksCrawler
    license_review_status = "who_source_terms_pending_review"
    observation_description = "current-week reporting-site outbreak case/death matrix cells"

    def __init__(self, *, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        super().__init__(full_history_start_year=full_history_start_year, **kwargs)

    def prepare_rows(self, rows, *, source="all", start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        first = int(self.full_history_start_year if start_year is None else start_year)
        selected = [row for row in rows if int(row["SourceReportYear"]) >= first]
        latest = max((date.fromisoformat(row["Date"]) for row in selected), default=None)
        audit = dict(source_audit or {})
        audit.update(observations=len(selected), coverage_complete=False)
        return MYWHOAnnualFetchResult(selected, latest, self.output_csv, [
            f"[source] {DEFAULT_SOURCE_NAME}: {len(selected)} exact weekly matrix observations",
            "[coverage] partial: rotated/unreadable matrices, cumulative counts and historical revision notes are not inferred",
            "[release] independent WHO source identity; pending reviewed registry mappings and public snapshot",
        ], audit)
