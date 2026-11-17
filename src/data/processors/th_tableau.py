"""Tableau holdings remain distinct from PDFs, old Report 506 and ID_TH snapshots."""
from pathlib import Path

from src.data.crawlers.th_tableau import (
    DEFAULT_SCOPE,
    DEFAULT_SOURCE_NAME,
    ONTOLOGY_SOURCE_ID,
)
from src.data.processors.th_dds import ThailandDDSWeeklyUpdater


class ThailandDDSTableauUpdater(ThailandDDSWeeklyUpdater):
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    mapping_notifications_enabled = False
    public_release_enabled = False

    def __init__(self, *, output_csv=None):
        root = Path(__file__).resolve().parents[3]
        self.output_csv = Path(output_csv or root / "data/current/th/thailand_dds_tableau_weekly.csv")
