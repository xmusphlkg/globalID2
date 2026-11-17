"""Source-native updater for INS SIVIGILA's 2023 onward dataset."""
from __future__ import annotations

from pathlib import Path

from src.data.crawlers.co_current import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    HISTORY_START_YEAR,
    ONTOLOGY_SOURCE_ID,
)
from src.data.processors.co import ColombiaINSSivigilaUpdater


class ColombiaINSSivigilaCurrentUpdater(ColombiaINSSivigilaUpdater):
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    license_review_status = "CC BY 4.0; aggregation scope review pending"

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        super().__init__(
            output_csv=output_csv or Path(__file__).resolve().parents[3] /
            "data/current/co/colombia_sivigila_current.csv",
            full_history_start_year=full_history_start_year,
            **kwargs,
        )

    @staticmethod
    def normalize_source(source):
        if str(source or "all").lower() not in {"all", "co", "colombia", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported Colombia current source: {source!r}")
