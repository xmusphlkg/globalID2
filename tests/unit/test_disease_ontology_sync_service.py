from __future__ import annotations

from datetime import date

from src.ontology import load_disease_ontology
from src.services.disease_ontology_sync_service import (
    build_disease_ontology_sync_payload,
)


def _one(rows: list[dict], key: str, value: str) -> dict:
    return next(row for row in rows if row[key] == value)


def test_sync_payload_preserves_facets_multi_parent_edges_and_no_rollup() -> None:
    ontology = load_disease_ontology()
    payload = build_disease_ontology_sync_payload(ontology)

    document = ontology.to_dict()
    assert len(payload.taxonomy_nodes) == sum(
        len(facet["tags"]) for facet in document["facets"]
    )
    perinatal_parents = {
        row["parent_node_code"]
        for row in payload.taxonomy_edges
        if row["child_node_code"] == "clinical_course.perinatal_hepatitis"
    }
    assert perinatal_parents == {
        "clinical_course.hepatitis",
        "clinical_course.perinatal_condition",
    }
    assert {row["aggregation_policy"] for row in payload.taxonomy_edges} == {
        "none"
    }
    assert {row["aggregation_policy"] for row in payload.concept_relations} == {
        "non_additive"
    }


def test_sync_payload_keeps_group_residual_series_out_of_flat_concepts() -> None:
    payload = build_disease_ontology_sync_payload(load_disease_ontology())

    au_nec = _one(
        payload.surveillance_series,
        "series_code",
        "SER_AU_VIRAL_HEPATITIS_NEC",
    )
    assert au_nec["disease_id"] is None
    assert au_nec["target_group_code"] == "G_VIRAL_HEPATITIS"
    assert au_nec["comparability"] == "not_comparable"
    assert au_nec["aggregation_policy"] == "non_additive"


def test_sync_payload_persists_negative_source_availability_without_series() -> None:
    payload = build_disease_ontology_sync_payload(load_disease_ontology())

    us_hiv = _one(payload.source_availability, "availability_code", "AV_US_HIV")
    assert us_hiv["target_code"] == "D162"
    assert us_hiv["status"] == "not_reported_by_source"
    assert us_hiv["series_code"] is None

    chronic_b = [
        row
        for row in payload.surveillance_series
        if row["disease_id"] == "D208" and row["country_code"] == "US"
    ]
    assert len(chronic_b) == 2
    assert {row["source_series_code"] for row in chronic_b} == {
        "SER_US_CHRONIC_HEPATITIS_B_CONFIRMED",
        "SER_US_CHRONIC_HEPATITIS_B_PROBABLE",
    }


def test_sync_payload_preserves_nhss_measure_population_and_lifecycle() -> None:
    payload = build_disease_ontology_sync_payload(load_disease_ontology())

    hiv = _one(
        payload.surveillance_series, "series_code", "SER_US_NHSS_HIV_ANNUAL"
    )
    aids = _one(
        payload.surveillance_series, "series_code", "SER_US_NHSS_AIDS_ANNUAL"
    )
    aids_availability = _one(
        payload.source_availability, "availability_code", "AV_US_NHSS_AIDS"
    )

    assert hiv["metric_type"] == "hiv_diagnoses"
    assert hiv["valid_from"] == date(2014, 1, 1)
    assert hiv["metadata"]["facet_tags"]["population"] == [
        "population.age_13_plus"
    ]
    assert aids["metric_type"] == "aids_classifications"
    assert aids["availability_status"] == "historical"
    assert aids["valid_to"] == date(2022, 12, 31)
    assert aids["is_active"] is False
    assert aids_availability["valid_to"] == date(2022, 12, 31)
    assert aids_availability["is_active"] is False


def test_sync_payload_uses_release_version_and_series_reporting_semantics() -> None:
    ontology = load_disease_ontology()
    payload = build_disease_ontology_sync_payload(ontology)
    document = ontology.to_dict()

    for series in payload.surveillance_series:
        registered = next(
            item for item in document["source_series"]
            if item["id"] == series["series_code"]
        )
        assert series["definition_version"] == registered.get(
            "definition_version",
            f"{document['registry_id']}:{document['release_version']}",
        )

    registry_by_id = {item["id"]: item for item in document["source_series"]}
    for row in payload.surveillance_series:
        registered = registry_by_id[row["series_code"]]
        assert row["reporting_basis"] == registered.get(
            "reporting_basis",
            (
                "sentinel_surveillance"
                if "sentinel" in registered["measure"].casefold()
                else "survey_or_screening"
                if any(
                    token in registered["measure"].casefold()
                    for token in ("survey", "screening")
                )
                else "diagnosis_or_classification"
                if any(
                    token in registered["measure"].casefold()
                    for token in ("diagnos", "classification")
                )
                else "notification"
            ),
        )
        assert row["unit"] == registered.get("unit", "count")


def test_sync_payload_does_not_infer_cross_source_comparability() -> None:
    payload = build_disease_ontology_sync_payload(load_disease_ontology())

    concept_rows = [row for row in payload.surveillance_series if row["disease_id"]]
    assert concept_rows
    assert all(row["comparability"] != "direct" for row in concept_rows)
    sentinel = _one(
        payload.surveillance_series,
        "series_code",
        "SER_JP_GAS_PHARYNGITIS_SENTINEL_WEEKLY",
    )
    assert sentinel["reporting_basis"] == "sentinel_surveillance"
