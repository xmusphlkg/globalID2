"""PHAC rolling weekly national laboratory data, no patient-case projection."""
from pathlib import Path

from src.data.crawlers.ca_respiratory_weekly import (
    DEFAULT_SCOPE, ONTOLOGY_SOURCE_ID, CARespiratoryWeeklyCrawler,
)
from src.data.processors.my_who_flunet import MYWHOFluNetUpdater


class CARespiratoryWeeklyUpdater(MYWHOFluNetUpdater):
    country_code = "CA"
    series_country_code = "CA"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:CA:national"
    source_aliases = {"all", "ca", DEFAULT_SCOPE}
    public_release_enabled = True
    license_review_status = "operator_authorized_public_release"
    default_crawler_type = CARespiratoryWeeklyCrawler
    default_output_csv = Path(__file__).resolve().parents[3] / "data/current/ca/phac_respiratory_weekly.csv"
    observation_description = "PHAC national weekly laboratory detections, tests and positivity"
