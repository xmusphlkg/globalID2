"""Independent closed-year microdata source, retained privately by strata."""
from pathlib import Path

from src.data.crawlers.co_microdatos import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    ONTOLOGY_SOURCE_ID,
)
from src.data.processors.co import ColombiaINSSivigilaUpdater


class ColombiaINSMicrodataUpdater(ColombiaINSSivigilaUpdater):
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    license_review_status = "source_terms_review_pending"

    def __init__(self, *, output_csv=None, full_history_start_year=2025, **kwargs):
        super().__init__(output_csv=output_csv or Path(__file__).resolve().parents[3] /
                         "data/current/co/microdatos-weekly/weekly_notification_records.csv",
                         full_history_start_year=full_history_start_year, **kwargs)

    @staticmethod
    def normalize_source(source):
        if source != DEFAULT_SCOPE:
            raise ValueError("An explicit Colombia microdata source is required")
