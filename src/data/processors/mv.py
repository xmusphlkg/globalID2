"""Source-native Maldives HPA monthly observations."""
from datetime import date
from pathlib import Path

from src.data.crawlers.mv import DEFAULT_SCOPE, ONTOLOGY_SOURCE_ID, HISTORY_START_YEAR, MaldivesHPACrawler
from src.data.processors.my_who_annual import MYWHOAnnualUpdater, MYWHOAnnualFetchResult


class MaldivesHPAUpdater(MYWHOAnnualUpdater):
    requires_verified_observations = True
    country_code = "MV"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:MV:national"
    source_aliases = {"all", "mv", "maldives", DEFAULT_SCOPE}
    license_review_status = "hpa_reuse_terms_unverified"
    default_output_csv = Path("data/current/mv/maldives_hpa_communicable.csv")
    default_crawler_type = MaldivesHPACrawler

    def __init__(self, *, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        super().__init__(full_history_start_year=full_history_start_year, **kwargs)

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        selected = [r for r in rows if int(r["SourceReportYear"]) >= int(start_year or self.full_history_start_year)]
        audit = dict(source_audit or {})
        audit["observations"] = len(selected)
        # An empty captured catalogue is evidence of an unavailable feed, never
        # a zero-count month or successful numerical coverage.
        return MYWHOAnnualFetchResult(selected, max((date.fromisoformat(r["Date"]) for r in selected), default=None),
            self.output_csv, [f"[source] Maldives HPA: {len(selected)} verified monthly observations",
                              f"[coverage] {audit.get('status', 'partial')}; no public SIDAS API verified"], audit)
