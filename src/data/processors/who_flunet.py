"""Country-bound WHO FluNet updater; retain the existing Malaysia identity."""
from dataclasses import replace
from pathlib import Path

from src.data.crawlers.who_flunet import (
    DEFAULT_SCOPE,
    HISTORY_START_YEAR,
    WHOFluNetCrawler,
    country_contract,
)
from src.data.processors.my_who_flunet import MYWHOFluNetUpdater

ROOT = Path(__file__).resolve().parents[3]


class WHOFluNetUpdater(MYWHOFluNetUpdater):
    public_release_enabled = True
    license_review_status = "operator_authorized_public_release"

    def __init__(self, country_code, *, output_csv=None,
                 full_history_start_year=HISTORY_START_YEAR,
                 refresh_recent_months=3, crawler_type=None):
        self.country_code, contract = country_contract(country_code)
        self.series_country_code = self.country_code
        self.source_scope = DEFAULT_SCOPE
        self.ontology_source_id = contract["source_id"]
        self.series_geography_key = f"country:{self.country_code}:national"
        self.source_aliases = {"all", self.country_code.lower(), DEFAULT_SCOPE}
        self.default_output_csv = ROOT / f"data/current/{self.country_code.lower()}/who_flunet_weekly.csv"
        self.default_crawler_type = WHOFluNetCrawler
        super().__init__(output_csv=output_csv, full_history_start_year=full_history_start_year,
                         refresh_recent_months=refresh_recent_months, crawler_type=crawler_type)

    def normalize_source(self, source):
        if str(source or "all").strip().casefold() not in self.source_aliases:
            raise ValueError(f"Unsupported {self.country_code} FluNet source: {source!r}")

    def prepare_rows(self, rows, *, source="all", start_year=None, end_year=None, **options):
        # ISO week 1 can begin in the previous Gregorian year (2025 W1 starts
        # 2024-12-30). Source-year filters must not silently lose that week.
        first = int(start_year if start_year is not None else self.full_history_start_year)
        result = super().prepare_rows(rows, source=source, start_year=max(1900, first - 1), **options)
        selected = [row for row in result.rows if int(row["SourceYear"]) >= first
                    and (end_year is None or int(row["SourceYear"]) <= int(end_year))]
        return replace(result, rows=selected)
