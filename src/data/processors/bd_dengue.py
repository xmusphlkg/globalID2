"""DGHS admissions and deaths retain independent source-native series."""
from datetime import date
from pathlib import Path

from src.data.crawlers.bd_dengue import DEFAULT_SCOPE, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID, BangladeshDGHSDengueCrawler
from src.data.processors.my_who_annual import MYWHOAnnualFetchResult, MYWHOAnnualUpdater


class BangladeshDGHSDengueUpdater(MYWHOAnnualUpdater):
    country_code = "BD"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = None
    series_geography_from_rows = True
    source_aliases = {"all", DEFAULT_SCOPE, "bd_dghs", "dghs_dengue"}
    default_output_csv = Path(__file__).resolve().parents[3] / "data/current/bd/dghs_dengue_daily.csv"
    default_crawler_type = BangladeshDGHSDengueCrawler
    license_review_status = "pending_source_reuse_review"

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        super().__init__(output_csv=output_csv, full_history_start_year=full_history_start_year, **kwargs)

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, start_year=None, months=None, source_audit=None, **options):
        self.normalize_source(source)
        first_year = self.full_history_start_year if start_year is None else int(start_year)
        selected = [row for row in rows if int(row["Year"]) >= first_year and (not months or (int(row["Year"]), int(row["Month"])) in months)]
        if not selected:
            raise ValueError("No verified DGHS dengue observations in requested window")
        return MYWHOAnnualFetchResult(selected, max(date.fromisoformat(row["Date"]) for row in selected), self.output_csv,
            [f"BD DGHS: {len(selected)} source-native daily/cumulative admission/death observations", "[coverage] Reporting hospitals; admissions are not population incidence; cumulative values never differenced", "[release] Pending source reuse and verified country snapshot"], source_audit or {})
