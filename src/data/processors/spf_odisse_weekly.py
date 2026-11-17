"""SPF weekly territory activity rates, retaining source denominators."""
from pathlib import Path

from src.data.crawlers.spf_odisse_weekly import DEFAULT_SCOPE, HISTORY_START_YEAR, SPFOdisseWeeklyCrawler, country_spec
from src.data.processors.my_who_flunet import MYWHOFluNetUpdater


class SPFOdisseWeeklyUpdater(MYWHOFluNetUpdater):
    source_scope = DEFAULT_SCOPE
    public_release_enabled = True
    license_review_status = "etalab_open_licence_2_0_attribution_required"
    default_crawler_type = SPFOdisseWeeklyCrawler
    observation_description = "SPF weekly SurSaUD territory healthcare activity rates, not patient notifications"

    def __init__(self, country_code, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        country_spec(country_code)
        self.country_code = country_code.upper()
        self.series_country_code = self.country_code
        self.ontology_source_id = f"SRC_{self.country_code}_SPF_ODISSE_WEEKLY"
        self.series_geography_key = f"country:{self.country_code}:national"
        self.source_aliases = {"all", self.country_code.lower(), DEFAULT_SCOPE}
        self.default_output_csv = Path(__file__).resolve().parents[3] / f"data/current/{self.country_code.lower()}/spf_odisse_weekly.csv"
        super().__init__(output_csv=output_csv, full_history_start_year=full_history_start_year, **kwargs)

    def normalize_source(self, source):
        if str(source or "all").strip().casefold() not in self.source_aliases:
            raise ValueError(f"Unsupported {self.country_code} SPF weekly source: {source!r}")
