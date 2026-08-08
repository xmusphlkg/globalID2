from __future__ import annotations

import pytest

from scripts.generate_site_data import (
    enrich_diseases_with_ontology,
    validate_record_catalogue_coverage,
)
from src.ontology import load_disease_ontology


def test_export_catalogue_entries_receive_compact_ontology_metadata() -> None:
    diseases = [
        {"disease_id": "D005", "name_en": "AIDS"},
        {"disease_id": "D014", "name_en": "H5N1"},
    ]

    enriched = enrich_diseases_with_ontology(diseases, load_disease_ontology())

    assert enriched[0]["ontology"]["rollup_policy"] == "no_auto_rollup"
    assert enriched[0]["ontology"]["facet_tags"]["clinical_course"] == [
        "clinical_course.aids"
    ]
    assert "ontology" not in enriched[1]


def test_export_rejects_unknown_and_unmigrated_deprecated_fact_ids() -> None:
    with pytest.raises(RuntimeError, match="missing from standard_diseases.csv: D404"):
        validate_record_catalogue_coverage(
            [{"disease_id": "D404"}],
            {"D005", "D999"},
            {"D005"},
        )

    with pytest.raises(RuntimeError, match="migration before export: D067"):
        validate_record_catalogue_coverage(
            [{"disease_id": "D067"}],
            {"D007", "D067", "D999"},
            {"D007"},
        )


def test_export_allows_non_public_total_rows_to_be_ignored() -> None:
    validate_record_catalogue_coverage(
        [{"disease_id": "D999"}],
        {"D005", "D999"},
        {"D005"},
    )
