"""Tests for the JSON-backed disease ontology registry."""

from __future__ import annotations

import copy
import csv
from collections import defaultdict
from pathlib import Path

import pytest

from src.ontology import (
    NO_AUTO_ROLLUP,
    DiseaseOntology,
    OntologyValidationError,
    load_disease_ontology,
)


MAPPING_DIR = Path(__file__).resolve().parents[2] / "configs" / "mapping"


EXPECTED_CONCEPT_IDS = {
    "D005",
    "D162",
    "D006",
    "D007",
    "D008",
    "D009",
    "D010",
    "D011",
    "D012",
    "D067",
    "D068",
    "D069",
    "D070",
    "D071",
    "D208",
    "D209",
    "D210",
    "D211",
    "D212",
    "D213",
    "D214",
    "D215",
    "D216",
    "D217",
    "D218",
    "D219",
    "D220",
    "D221",
    "D222",
    "D223",
    "D224",
    "D225",
    "D226",
    "D227",
    "D228",
    "D229",
    "D230",
    "D231",
    "D232",
    "D233",
}


@pytest.fixture
def registry() -> DiseaseOntology:
    return load_disease_ontology()


@pytest.fixture
def document(registry: DiseaseOntology) -> dict:
    return registry.to_dict()


def _find(items: list[dict], entity_id: str) -> dict:
    return next(item for item in items if item["id"] == entity_id)


def _count_tree_tag(nodes: list[dict], tag_id: str) -> int:
    return sum(
        (1 if node["id"] == tag_id else 0) + _count_tree_tag(node["children"], tag_id)
        for node in nodes
    )


def _explicit_mapping_series_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for mapping_path in sorted(MAPPING_DIR.glob("*.csv")):
        with mapping_path.open(encoding="utf-8-sig", newline="") as handle:
            for line_number, raw in enumerate(csv.DictReader(handle), start=2):
                series_id = (raw.get("series_id") or "").strip()
                if not series_id:
                    continue
                rows.append(
                    {
                        "path": str(mapping_path.relative_to(MAPPING_DIR.parent.parent)),
                        "line": str(line_number),
                        "series_id": series_id,
                        "source_id": (raw.get("source_id") or "").strip(),
                        "disease_id": (raw.get("disease_id") or "").strip(),
                        "notes": (raw.get("notes") or "").strip(),
                    }
                )
    return rows


def test_explicit_mapping_series_are_consistent_with_ontology(
    registry: DiseaseOntology,
) -> None:
    """Keep CSV compatibility mappings aligned with source-series truth."""
    ontology_series = {
        item["id"]: item for item in registry.to_dict()["source_series"]
    }
    targets_by_series: dict[str, set[str]] = defaultdict(set)
    errors: list[str] = []

    mapping_rows = _explicit_mapping_series_rows()
    assert mapping_rows, "expected at least one explicit mapping series_id"

    for row in mapping_rows:
        location = f"{row['path']}:{row['line']}"
        series_id = row["series_id"]
        source_id = row["source_id"]
        disease_id = row["disease_id"]
        targets_by_series[series_id].add(disease_id)

        series = ontology_series.get(series_id)
        if series is None:
            errors.append(f"{location}: unknown ontology series {series_id}")
            continue
        if series.get("source_id") != source_id:
            errors.append(
                f"{location}: {series_id} source_id is {source_id!r} in mapping "
                f"but {series.get('source_id')!r} in ontology"
            )
        is_declared_group_compatibility = bool(series.get("group_id")) and (
            "legacy flat-target compatibility" in row["notes"].casefold()
        )
        if (
            series.get("concept_id") != disease_id
            and not is_declared_group_compatibility
        ):
            errors.append(
                f"{location}: {series_id} targets {disease_id!r} in mapping "
                "but ontology target is "
                f"concept={series.get('concept_id')!r}, group={series.get('group_id')!r}"
            )

    for series_id, disease_ids in sorted(targets_by_series.items()):
        if len(disease_ids) > 1:
            errors.append(
                f"{series_id} is reused across mapping disease targets: "
                f"{', '.join(sorted(disease_ids))}"
            )

    assert not errors, "mapping/ontology series inconsistencies:\n" + "\n".join(errors)


def test_default_registry_covers_hiv_and_hepatitis_without_auto_rollup(
    registry: DiseaseOntology,
) -> None:
    assert EXPECTED_CONCEPT_IDS <= set(registry.concept_ids)

    document = registry.to_dict()
    assert document["default_rollup_policy"] == NO_AUTO_ROLLUP
    for collection in ("concepts", "groups", "relations", "source_series"):
        assert {item["rollup_policy"] for item in document[collection]} == {
            NO_AUTO_ROLLUP
        }
    assert all(relation["rollup"] is False for relation in document["relations"])

    statuses = {item["id"]: item["status"] for item in document["concepts"]}
    assert not {
        concept_id for concept_id, status in statuses.items() if status == "reserved"
    }
    assert {statuses[concept_id] for concept_id in {"D208", "D209", "D210", "D211", "D212"}} == {
        "active"
    }


def test_hiv_concepts_are_distinct_but_related(registry: DiseaseOntology) -> None:
    aids = registry.concept_detail("D005")
    hiv = registry.concept_detail("D162")

    assert aids["labels"]["en"] == "AIDS"
    assert "HIV/AIDS" in aids["legacy_labels"]["en"]
    assert hiv["labels"]["en"] == "HIV infection"
    assert aids["id"] != hiv["id"]
    assert "G_HIV_SPECTRUM" in aids["group_ids"]
    assert "G_HIV_SPECTRUM" in hiv["group_ids"]
    assert [item["id"] for item in aids["relations"]["outgoing"]] == [
        "R_D005_STAGE_OF_D162"
    ]
    assert any(item["source"]["country_code"] == "TW" for item in hiv["source_series"])


def test_hepatitis_redirects_and_course_qualified_concepts(
    registry: DiseaseOntology,
) -> None:
    legacy_other = registry.concept_detail("D012")
    legacy_acute_a = registry.concept_detail("D067")

    assert legacy_other["status"] == "deprecated"
    assert legacy_acute_a["status"] == "deprecated"
    assert {
        (item["id"], item["to_ref"]["id"])
        for detail in (legacy_other, legacy_acute_a)
        for item in detail["relations"]["outgoing"]
        if item["type"] == "replaced_by"
    } == {
        ("R_D012_REPLACED_BY_D071", "D071"),
        ("R_D067_REPLACED_BY_D007", "D007"),
    }
    assert registry.concept_detail("D008")["facet_tags"]["clinical_course"] == [
        "clinical_course.unspecified_hepatitis"
    ]
    assert registry.concept_detail("D009")["facet_tags"]["clinical_course"] == [
        "clinical_course.unspecified_hepatitis"
    ]
    assert registry.concept_detail("D208")["facet_tags"]["clinical_course"] == [
        "clinical_course.chronic_hepatitis"
    ]
    assert registry.concept_detail("D210")["facet_tags"]["clinical_course"] == [
        "clinical_course.acute_hepatitis"
    ]


def test_facet_tree_preserves_nested_multi_parent_dag(
    registry: DiseaseOntology,
) -> None:
    etiology = registry.facet_tree("etiology")
    clinical = registry.facet_tree("clinical_course")

    assert etiology["tag_count"] >= 11
    viral = _find(etiology["roots"], "etiology.viral")
    retroviral = _find(viral["children"], "etiology.retroviral")
    assert _find(retroviral["children"], "etiology.hiv")["labels"]["en"] == (
        "Human immunodeficiency virus"
    )
    assert (
        _count_tree_tag(clinical["roots"], "clinical_course.perinatal_hepatitis") == 2
    )


def test_group_detail_resolves_direct_members_and_subgroups(
    registry: DiseaseOntology,
) -> None:
    family = registry.group_detail("G_VIRAL_HEPATITIS")
    hbv = registry.group_detail("G_HBV_SPECTRUM")

    assert [item["id"] for item in family["concepts"]] == ["D006"]
    assert {item["id"] for item in family["subgroups"]} >= {
        "G_HEPATITIS_BY_VIRUS",
        "G_HEPATITIS_BY_COURSE",
    }
    assert {item["id"] for item in hbv["concepts"]} == {
        "D008",
        "D068",
        "D208",
        "D209",
    }
    assert hbv["parent_group_ids"] == ["G_VIRAL_HEPATITIS"]


def test_series_lookup_resolves_source_target_facets_and_availability(
    registry: DiseaseOntology,
) -> None:
    series = registry.series_lookup("SER_TW_AIDS_042")

    assert series["source"]["country_code"] == "TW"
    assert series["target_ref"] == {"kind": "concept", "id": "D005"}
    assert series["target"]["labels"]["en"] == "AIDS"
    assert series["availability"][0]["status"] == "available"

    same_source_code = registry.series_lookup(
        source_id="SRC_US_NNDSS", local_code="10100"
    )
    assert {item["id"] for item in same_source_code} == {
        "SER_US_ACUTE_HEPATITIS_B_CONFIRMED",
        "SER_US_ACUTE_HEPATITIS_B_PROBABLE",
    }
    label_match = registry.series_lookup(
        country_code="au", local_label="hepatitis c (newly acquired)"
    )
    assert [item["concept_id"] for item in label_match] == ["D210"]

    assert {
        item["id"]
        for item in registry.series_lookup(
            source_id="SRC_US_NNDSS", local_code="10106"
        )
    } == {
        "SER_US_CHRONIC_HEPATITIS_C_CONFIRMED",
        "SER_US_CHRONIC_HEPATITIS_C_PROBABLE",
    }
    assert [
        item["id"]
        for item in registry.series_lookup(
            source_id="SRC_US_NNDSS", local_code="50248"
        )
    ] == ["SER_US_PERINATAL_HEPATITIS_C"]


def test_availability_distinguishes_source_absence_from_available_series(
    registry: DiseaseOntology,
) -> None:
    us_hiv = registry.availability_lookup(country_code="US", concept_id="D162")
    tw_hiv = registry.availability_lookup(country_code="TW", concept_id="D162")

    us_hiv_by_source = {item["source_id"]: item for item in us_hiv}
    assert us_hiv_by_source["SRC_US_NNDSS"]["status"] == "not_reported_by_source"
    assert "series_id" not in us_hiv_by_source["SRC_US_NNDSS"]
    assert us_hiv_by_source["SRC_US_NHSS"]["status"] == "available"
    assert us_hiv_by_source["SRC_US_NHSS"]["series_id"] == (
        "SER_US_NHSS_HIV_ANNUAL"
    )
    assert tw_hiv[0]["status"] == "available"
    assert tw_hiv[0]["series_id"] == "SER_TW_HIV_044"


def test_mapping_quality_hierarchy_has_parent_to_child_edges(
    registry: DiseaseOntology,
) -> None:
    hierarchy = registry.mapping_quality_hierarchy()

    assert "D006" in hierarchy["aggregate_ids"]
    assert {(edge["parent_id"], edge["child_id"]) for edge in hierarchy["edges"]} >= {
        ("D162", "D005"),
        ("D006", "D007"),
        ("D006", "D008"),
        ("D008", "D068"),
        ("D008", "D208"),
        ("D009", "D210"),
        ("D006", "D070"),
    }


def test_mapping_quality_series_registry_exports_exact_targets_and_statuses(
    registry: DiseaseOntology,
) -> None:
    rows = registry.mapping_quality_series_registry()
    by_id = {row["series_id"]: row for row in rows}

    confirmed = by_id["SER_US_CHRONIC_HEPATITIS_B_CONFIRMED"]
    assert confirmed == {
        "country_code": "US",
        "source_id": "SRC_US_NNDSS",
        "series_id": "SER_US_CHRONIC_HEPATITIS_B_CONFIRMED",
        "disease_id": "D208",
        "local_codes": ["10105"],
        "local_labels": ["Hepatitis B, chronic, Confirmed"],
        "status": "active",
        "availability_statuses": ["available"],
    }
    assert by_id["SER_TW_AIDS_042"]["availability_statuses"] == ["available"]
    aggregate = by_id["SER_AU_VIRAL_HEPATITIS_NEC"]
    assert aggregate["group_id"] == "G_VIRAL_HEPATITIS"
    assert "disease_id" not in aggregate
    assert all(
        ("disease_id" in row) != ("group_id" in row)
        for row in rows
    )
    assert rows == sorted(
        rows,
        key=lambda row: (
            row["country_code"],
            row["source_id"],
            row["series_id"],
        ),
    )


def test_ontology_accepts_iso_subdivision_source_jurisdiction(document: dict) -> None:
    source = _find(document["sources"], "SRC_CA_ON_PHO_IDTO")
    source["country_code"] = "CA-ON"

    registry = DiseaseOntology.from_dict(document)

    ontario = registry.series_lookup(
        country_code="ca-on",
        series_id=None,
        source_id="SRC_CA_ON_PHO_IDTO",
    )
    assert ontario
    assert {item["source"]["country_code"] for item in ontario} == {"CA-ON"}
    assert {item["geography_key"] for item in ontario} == {
        "country:CA-ON:national"
    }
    assert {
        availability["status"]
        for item in ontario
        for availability in item["availability"]
    } == {"available"}


@pytest.mark.parametrize("invalid_code", ["ca-on", "CA--ON", "CAN-ON", "CA-ONTARIO"])
def test_ontology_rejects_invalid_source_jurisdiction_codes(
    document: dict, invalid_code: str
) -> None:
    _find(document["sources"], "SRC_CA_ON_PHO_IDTO")["country_code"] = invalid_code

    with pytest.raises(OntologyValidationError, match="jurisdiction code"):
        DiseaseOntology.from_dict(document)


@pytest.mark.parametrize(
    ("collection", "entity_id", "field", "bad_value", "message"),
    [
        ("groups", "G_HIV_SPECTRUM", "concept_ids", ["D999"], "unknown concept"),
        (
            "source_series",
            "SER_CH_AIDS_ANNUAL",
            "source_id",
            "SRC_MISSING",
            "unknown source",
        ),
        (
            "availability",
            "AV_CH_AIDS",
            "series_id",
            "SER_MISSING",
            "unknown series",
        ),
    ],
)
def test_reference_validation_rejects_unknown_entities(
    document: dict,
    collection: str,
    entity_id: str,
    field: str,
    bad_value: object,
    message: str,
) -> None:
    _find(document[collection], entity_id)[field] = bad_value

    with pytest.raises(OntologyValidationError, match=message):
        DiseaseOntology.from_dict(document)


def test_concept_tag_and_relation_references_are_validated(document: dict) -> None:
    broken_tag = copy.deepcopy(document)
    _find(broken_tag["concepts"], "D005")["facet_tags"]["etiology"] = [
        "etiology.missing"
    ]
    with pytest.raises(OntologyValidationError, match="unknown tag"):
        DiseaseOntology.from_dict(broken_tag)

    broken_relation = copy.deepcopy(document)
    _find(broken_relation["relations"], "R_D005_STAGE_OF_D162")["to_ref"] = {
        "kind": "concept",
        "id": "D999",
    }
    with pytest.raises(OntologyValidationError, match="unknown concept"):
        DiseaseOntology.from_dict(broken_relation)


def test_tag_parent_must_belong_to_same_facet(document: dict) -> None:
    etiology = _find(document["facets"], "etiology")
    hiv = _find(etiology["tags"], "etiology.hiv")
    hiv["parent_ids"] = ["clinical_course.infection"]

    with pytest.raises(OntologyValidationError, match="belongs to facet"):
        DiseaseOntology.from_dict(document)


def test_facet_tag_cycle_is_rejected(document: dict) -> None:
    etiology = _find(document["facets"], "etiology")
    viral = _find(etiology["tags"], "etiology.viral")
    viral["parent_ids"] = ["etiology.hiv"]

    with pytest.raises(OntologyValidationError, match="cycle in facet tag graph"):
        DiseaseOntology.from_dict(document)


def test_group_cycle_is_rejected(document: dict) -> None:
    hiv_group = _find(document["groups"], "G_HIV_SPECTRUM")
    hiv_group["subgroup_ids"] = ["G_HIV_SPECTRUM"]

    with pytest.raises(OntologyValidationError, match="cycle in group graph"):
        DiseaseOntology.from_dict(document)


def test_hierarchical_relation_cycle_is_rejected(document: dict) -> None:
    document["relations"].append(
        {
            "id": "R_D162_STAGE_PARENT_OF_D005",
            "type": "clinical_stage_of",
            "from_ref": {"kind": "concept", "id": "D162"},
            "to_ref": {"kind": "concept", "id": "D005"},
            "hierarchical": True,
            "rollup": False,
            "rollup_policy": NO_AUTO_ROLLUP,
        }
    )

    with pytest.raises(
        OntologyValidationError, match="cycle in hierarchical relation graph"
    ):
        DiseaseOntology.from_dict(document)


def test_series_target_and_availability_target_must_match(document: dict) -> None:
    _find(document["availability"], "AV_CH_AIDS")["target_ref"] = {
        "kind": "concept",
        "id": "D162",
    }

    with pytest.raises(OntologyValidationError, match="target does not match"):
        DiseaseOntology.from_dict(document)


def test_source_code_cannot_cross_canonical_targets(document: dict) -> None:
    chronic_c = _find(
        document["source_series"], "SER_US_CHRONIC_HEPATITIS_C_CONFIRMED"
    )
    chronic_c["local_codes"] = ["10105"]

    with pytest.raises(OntologyValidationError, match="maps to multiple targets"):
        DiseaseOntology.from_dict(document)


def test_series_and_availability_validity_ranges_are_validated(document: dict) -> None:
    invalid_date = copy.deepcopy(document)
    series = _find(invalid_date["source_series"], "SER_US_NHSS_HIV_ANNUAL")
    series["valid_to"] = "not-a-date"
    with pytest.raises(OntologyValidationError, match="ISO date"):
        DiseaseOntology.from_dict(invalid_date)

    invalid_range = copy.deepcopy(document)
    availability = _find(invalid_range["availability"], "AV_US_NHSS_HIV")
    availability["valid_to"] = "2013-12-31"
    with pytest.raises(OntologyValidationError, match="invalid validity range"):
        DiseaseOntology.from_dict(invalid_range)


def test_series_reporting_semantics_are_validated(document: dict) -> None:
    series = _find(document["source_series"], "SER_US_NHSS_HIV_ANNUAL")
    series["comparability"] = "looks_close_enough"

    with pytest.raises(OntologyValidationError, match="comparability must be one of"):
        DiseaseOntology.from_dict(document)


def test_not_reported_availability_cannot_reference_a_series(document: dict) -> None:
    availability = _find(document["availability"], "AV_US_HIV")
    availability["series_id"] = "SER_US_NHSS_HIV_ANNUAL"

    with pytest.raises(OntologyValidationError, match="must not reference a series"):
        DiseaseOntology.from_dict(document)


def test_returned_objects_do_not_mutate_registry(registry: DiseaseOntology) -> None:
    detail = registry.concept_detail("D005")
    detail["labels"]["en"] = "changed"
    exported = registry.to_dict()
    _find(exported["concepts"], "D005")["labels"]["en"] = "also changed"

    assert registry.concept_detail("D005")["labels"]["en"] == "AIDS"
    with pytest.raises(KeyError, match="unknown concept id"):
        registry.concept_detail("D999")


def test_invalid_json_reports_location(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(OntologyValidationError, match="invalid ontology JSON"):
        DiseaseOntology.from_file(path)
