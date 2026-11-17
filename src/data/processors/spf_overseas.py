"""SPF territory updater contract for the shared source-series store."""
from pathlib import Path

from src.data.crawlers.spf_overseas import DEFAULT_SCOPE, HISTORY_START_YEAR, SPFOverseasCrawler, country_spec
from src.data.processors.my_who_annual import MYWHOAnnualUpdater


class SPFOverseasUpdater(MYWHOAnnualUpdater):
    source_scope = DEFAULT_SCOPE
    full_history_start_year = HISTORY_START_YEAR
    default_crawler_type = SPFOverseasCrawler
    license_review_status = 'etalab_open_licence_2_0_attribution_required'
    observation_description = 'SPF annual territory notifications, age and serogroup retained separately'

    def __init__(self, country_code, *, output_csv=None, **kwargs):
        country_spec(country_code)
        self.country_code = country_code.upper()
        self.series_country_code = self.country_code
        self.ontology_source_id = f'SRC_{self.country_code}_SPF_ODISSE'
        self.series_geography_key = f'country:{self.country_code}:national'
        self.source_aliases = {'all', self.country_code.lower(), DEFAULT_SCOPE}
        root = Path(__file__).resolve().parents[3]
        self.default_output_csv = root / 'data/current' / self.country_code.lower() / 'spf_odisse_annual.csv'
        kwargs.pop('full_history_start_year', None)
        super().__init__(output_csv=output_csv, full_history_start_year=HISTORY_START_YEAR, **kwargs)

    def normalize_source(self, source):
        if str(source or 'all').strip().casefold() not in self.source_aliases:
            raise ValueError(f'Unsupported {self.country_code} SPF source {source!r}')
