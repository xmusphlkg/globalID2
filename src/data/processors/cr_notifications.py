"""Registry-native Costa Rica national notification observations."""
from pathlib import Path
from datetime import date

from src.data.crawlers.cr_notifications import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, ONTOLOGY_SOURCE_ID, CostaRicaNotificationsCrawler
from src.data.processors.my_who_annual import MYWHOAnnualUpdater, MYWHOAnnualFetchResult


class CostaRicaNotificationsUpdater(MYWHOAnnualUpdater):
    country_code = "CR"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:CR:national"
    source_aliases = {"all", "cr", "costa rica", DEFAULT_SCOPE}
    license_review_status = "ministry_reuse_terms_unverified"
    default_output_csv = Path("data/current/cr/costa_rica_moh_notifications.csv")
    observation_description = "national annual notifications and cumulative publication snapshots"
    default_crawler_type = CostaRicaNotificationsCrawler

    def __init__(self, *, full_history_start_year=2014, **kwargs):
        super().__init__(full_history_start_year=full_history_start_year, **kwargs)

    def prepare_rows(self, rows, *, source="all", start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        first = int(start_year if start_year is not None else self.full_history_start_year)
        selected = [row for row in rows if int(row["SourceReportYear"]) >= first]
        if not selected:
            raise ValueError("No verified Costa Rica national notification table in captured reports")
        latest = max(date.fromisoformat(row["Date"]) for row in selected)
        audit = dict(source_audit or {})
        audit["observations"] = len(selected)
        return MYWHOAnnualFetchResult(selected, latest, self.output_csv,
            [f"[source] {DEFAULT_SOURCE_NAME}: {len(selected)} national observations",
             "[coverage] partial numerical coverage; annual counts and cumulative publication snapshots retain separate identities",
             "[release] pending verified mappings and public snapshot"], audit)
