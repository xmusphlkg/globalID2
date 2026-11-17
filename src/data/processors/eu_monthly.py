"""National single-month updater contracts for Czechia and Estonia."""
from pathlib import Path
from src.data.crawlers.eu_monthly import CONTRACTS, EUMonthlyCrawler
from .ecdc_respiratory import ECDCRespiratoryUpdater


class EUMonthlyUpdater(ECDCRespiratoryUpdater):
    def __init__(self, *, country_code, output_csv=None, full_history_start_year=None, refresh_recent_months=3, crawler_type=None):
        code = str(country_code).upper()
        if code not in CONTRACTS:
            raise ValueError("Only CZ/EE national monthly contracts are supported")
        self.country_code = code
        self.source_scope = CONTRACTS[code]["scope"]
        self.ontology_source_id = CONTRACTS[code]["source_id"]
        self.series_geography_key = f"country:{code}:national"
        self.output_csv = Path(output_csv or f"data/current/{code.lower()}/{self.source_scope}.csv")
        self.full_history_start_year = int(full_history_start_year or CONTRACTS[code]["start_year"])
        self.refresh_recent_months = int(refresh_recent_months)
        self.crawler_type = crawler_type or EUMonthlyCrawler

    def prepare_rows(self, rows, *, source=None, **options):
        return super().prepare_rows(rows, source=source or self.source_scope, **options)
