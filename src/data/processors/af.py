"""Registry-native Afghanistan NDSR weekly observations and source audit."""
from datetime import date
from pathlib import Path

from src.data.crawlers.af import (
    DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID,
    AfghanistanNDSRCrawler,
)
from src.data.processors.my_who_annual import MYWHOAnnualUpdater, MYWHOAnnualFetchResult


class AfghanistanNDSRUpdater(MYWHOAnnualUpdater):
    requires_verified_observations = True
    country_code = "AF"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "source:AF:ndsr:reporting_sites"
    source_aliases = {"all", "af", "afghanistan", DEFAULT_SCOPE}
    default_output_csv = Path("data/current/af/afghanistan_ndsr_weekly.csv")
    default_crawler_type = AfghanistanNDSRCrawler
    license_review_status = "moph_source_terms_unverified"
    observation_description = "source-defined weekly reporting-site cases and deaths"

    def __init__(self, *, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        super().__init__(full_history_start_year=full_history_start_year, **kwargs)

    def prepare_rows(self, rows, *, source="all", start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        first = int(self.full_history_start_year if start_year is None else start_year)
        selected = [row for row in rows if int(row["SourceReportYear"]) >= first]
        latest = max((date.fromisoformat(row["Date"]) for row in selected), default=None)
        audit = dict(source_audit or {})
        audit.update(observations=len(selected), coverage_complete=False)
        # A valid zero-row audit is useful when originals are blocked. Never
        # turn that into a successful imported observation or zero disease count.
        if not selected:
            blocked = not audit.get("captured_reports") and any(
                item.get("stage") in {"directory", "pdf"} for item in audit.get("failures", []))
            audit["status"] = "source_access_blocked" if blocked else "no_verified_observations"
        return MYWHOAnnualFetchResult(selected, latest, self.output_csv, [
            f"[source] {DEFAULT_SOURCE_NAME}: {len(selected)} observations",
            f"[coverage] {audit.get('status', 'partial')}; originals, successful parsing and numerical coverage are distinct",
            "[release] candidate numerical contract; public release disabled pending live original verification",
        ], audit)
