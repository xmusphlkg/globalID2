"""Country-specific WHO updater instances sharing the reviewed annual store contract."""
from pathlib import Path

from src.data.crawlers.who_annual import DEFAULT_SCOPE, HISTORY_START_YEAR, WHOAnnualCrawler, country_contract
from src.data.processors.my_who_annual import MYWHOAnnualUpdater

ROOT = Path(__file__).resolve().parents[3]


class WHOAnnualUpdater(MYWHOAnnualUpdater):
    """Retain every published year on refresh, including older source revisions."""
    def __init__(self, country_code, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR,
                 refresh_recent_months=3, crawler_type=None):
        self.country_code, contract = country_contract(country_code)
        self.source_scope = DEFAULT_SCOPE
        self.ontology_source_id = contract["source_id"]
        self.series_geography_key = f"country:{self.country_code}:national"
        self.source_aliases = {"all", self.country_code.lower(), DEFAULT_SCOPE}
        self.default_output_csv = ROOT / f"data/current/{self.country_code.lower()}/who_gho_annual.csv"
        self.default_crawler_type = WHOAnnualCrawler
        super().__init__(output_csv=output_csv, full_history_start_year=full_history_start_year,
                         refresh_recent_months=refresh_recent_months, crawler_type=crawler_type)

    def normalize_source(self, source):
        if str(source or "all").strip().casefold() not in self.source_aliases:
            raise ValueError(f"Unsupported {self.country_code} source: {source!r}")
