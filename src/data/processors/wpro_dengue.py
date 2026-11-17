"""Explicit WHO-distributed national dengue observations, separate from annual GHO."""
from datetime import date
from pathlib import Path
from src.data.crawlers.wpro_dengue import DEFAULT_SCOPE, HISTORY_START_YEAR, WPRODengueCrawler, country_contract
from src.data.processors.my_who_annual import MYWHOAnnualUpdater, MYWHOAnnualFetchResult


class WPRODengueUpdater(MYWHOAnnualUpdater):
    source_scope = DEFAULT_SCOPE
    license_review_status = 'who_source_terms_pending_review'
    def __init__(self, country_code, *, output_csv=None, **kwargs):
        self.country_code, contract = country_contract(country_code)
        self.ontology_source_id = f'SRC_{self.country_code}_WHO_WPRO_DENGUE'
        self.series_geography_key = f'country:{self.country_code}:national'
        self.source_aliases = {'all', DEFAULT_SCOPE, self.country_code.lower()}
        self.default_output_csv = Path(__file__).resolve().parents[3] / f'data/current/{self.country_code.lower()}/wpro_dengue.csv'
        self.default_crawler_type = WPRODengueCrawler
        super().__init__(output_csv=output_csv, full_history_start_year=HISTORY_START_YEAR, **kwargs)
    def normalize_source(self, source):
        if str(source or 'all').strip().casefold() not in self.source_aliases:
            raise ValueError('Unsupported WPRO dengue source')
    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, **options):
        self.normalize_source(source)
        start_year = self.full_history_start_year if start_year is None else int(start_year)
        selected = [row for row in rows if int(row['Year']) >= start_year]
        if not selected:
            raise ValueError('No verified WPRO dengue observations in requested window')
        return MYWHOAnnualFetchResult(selected, max(date.fromisoformat(row['Date']) for row in selected), self.output_csv,
                                     [f'{self.country_code}: explicit WHO WPRO dengue period counts and cumulative snapshots'])
