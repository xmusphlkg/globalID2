from __future__ import annotations

from scripts.sync_disease_ontology import (
    FACT_REMAPS,
    MAPPING_TRANSITIONS,
    SERIES_GEOGRAPHY_REMAPS,
    _br_repaired_metadata,
    _catalogue_ids,
    _fact_evidence_errors,
    _fact_filter_sql,
    _managed_mapping_rows,
    _mapping_deactivation_plan,
    _mapping_target_changes,
    _mapping_rows_to_deactivate,
    _partition_br_ntra_rows,
    _source_evidence_values,
    _series_geography_filter_sql,
    _series_geography_migration_key,
    build_plan,
)
from src.ontology import load_disease_ontology


def test_us_nndss_total_geography_remap_is_source_and_evidence_scoped() -> None:
    remap = next(
        item for item in SERIES_GEOGRAPHY_REMAPS if item.source_system == "SRC_US_NNDSS"
    )

    assert remap.old_key == "country:US:national"
    assert remap.new_key == "source:SRC_US_NNDSS:reporting-area:total"
    assert remap.evidence_fields == ("ReportingArea", "Reporting Area")
    assert remap.evidence_values == ("TOTAL",)
    selector = _series_geography_filter_sql(remap, alias="old_observation")
    assert "old_series.source_system = :source_system" in selector
    assert "old_observation.raw_data ->> 'ReportingArea'" in selector
    assert "lower(" in selector
    assert "ANY(:evidence_values)" in selector
    assert _series_geography_migration_key(remap).startswith(
        "series_geography:SRC_US_NNDSS:"
    )


def test_ontario_geography_remap_is_idempotent_and_preserves_source_geocode() -> None:
    remap = next(
        item
        for item in SERIES_GEOGRAPHY_REMAPS
        if item.source_system == "SRC_CA_ON_PHO_IDTO"
    )

    assert remap.series_pattern == "SER_CA_ON_PHO_IDTO_%"
    assert remap.old_key == "country:CA:subdivision:CA-ON"
    assert remap.new_key == "country:CA-ON:national"
    assert remap.evidence_fields == ("Geocode",)
    assert remap.evidence_values == ("CA-ON",)
    selector = _series_geography_filter_sql(remap, alias="old_observation")
    assert "old_series.source_system = :source_system" in selector
    assert "old_observation.raw_data ->> 'Geocode'" in selector
    assert "old_observation.geography_key = :old_key" in selector


def test_managed_mapping_rows_are_unique_and_keep_hiv_aids_distinct() -> None:
    managed_ids = _catalogue_ids()
    rows = _managed_mapping_rows(managed_ids)
    keys = [
        (
            row["disease_id"],
            row["country_code"],
            row["source_id"],
            row["local_name"],
        )
        for row in rows
    ]

    assert len(keys) == len(set(keys))
    assert not any(
        row["country_code"] == "US"
        and row["disease_id"] == "D005"
        and row["local_name"].casefold() == "hiv"
        for row in rows
    )
    assert any(
        row["disease_id"] == "D162" and row["local_name"].casefold() == "hiv"
        for row in rows
    )
    us_hiv = next(
        row
        for row in rows
        if row["country_code"] == "US"
        and row["disease_id"] == "D162"
        and row["local_name"] == "HIV diagnoses"
    )
    assert us_hiv["source_id"] == "SRC_US_NHSS"
    assert us_hiv["series_id"] == "SER_US_NHSS_HIV_ANNUAL"


def test_ontario_mapping_rows_use_filename_jurisdiction_scope() -> None:
    rows = [
        row
        for row in _managed_mapping_rows(_catalogue_ids())
        if row["source_id"] == "SRC_CA_ON_PHO_IDTO"
    ]

    assert rows
    assert {row["country_code"] for row in rows} == {"CA-ON"}
    assert {row["metadata"]["origin"] for row in rows} == {
        "configs/mapping/ca-on.csv"
    }


def test_partition_br_ntra_rows_removes_invalid_row_count_projection() -> None:
    raw_rows = [
        {"DiseaseCode": "NTRA", "Cases": "29"},
        {
            "DiseaseCode": "MENT",
            "RawDiseaseLabel": "Work-related mental disorder",
            "Cases": "7",
            "SourceFiles": "MENTBR20.dbc",
        },
    ]

    kept, removed, kept_cases, removed_cases = _partition_br_ntra_rows(raw_rows)

    assert [row["DiseaseCode"] for row in kept] == ["MENT"]
    assert [row["DiseaseCode"] for row in removed] == ["NTRA"]
    assert kept_cases == 7
    assert removed_cases == 29
    metadata = _br_repaired_metadata({"disease_codes": ["NTRA", "MENT"]}, kept)
    assert metadata["disease_codes"] == ["MENT"]
    assert metadata["source_files"] == ["MENTBR20.dbc"]
    assert metadata["ontology_semantic_repair"] == "BR_D193_REMOVE_NTRA"


def test_sync_plan_declares_br_ntra_semantic_repair() -> None:
    plan = build_plan()
    repair = plan["semantic_repairs"][0]

    assert plan["release_version"] == load_disease_ontology().to_dict()[
        "release_version"
    ]
    assert repair["country_code"] == "BR"
    assert repair["old_disease_id"] == "D193"
    assert repair["source_code"] == "NTRA"
    assert repair["action"] == "remove_invalid_legacy_projection"


def test_semantic_fact_remaps_are_source_scoped_and_evidence_guarded() -> None:
    """Semantic corrections must never degrade into broad D-code rewrites."""

    semantic_targets = {
        "D105",
        "D127",
        "D214",
        "D234",
        "D235",
        "D164",
        "D168",
        "D178",
        "D199",
        "D213",
        "D222",
        "D224",
        "D227",
    }
    semantic = [item for item in FACT_REMAPS if item.new_id in semantic_targets]

    assert semantic
    assert all(item.country_code for item in semantic)
    assert all(item.raw_label_pattern for item in semantic)
    assert all(item.evidence_field for item in semantic)
    assert all(item.evidence_value for item in semantic)
    assert any(
        item.country_code == "TW" and item.old_id == "D024" and item.new_id == "D105"
        for item in semantic
    )
    assert any(
        item.country_code == "NZ" and item.old_id == "D018" and item.new_id == "D127"
        for item in semantic
    )


def test_mapping_sync_deactivates_conflicting_bootstrap_and_removed_owned_rows() -> (
    None
):
    incoming = [
        {
            "country_code": "JP",
            "source_id": "SRC_JP_NIID",
            "local_name": "Bacterial dysentery",
            "disease_id": "D105",
        }
    ]
    existing = [
        {
            "id": 1,
            "country_code": "JP",
            "source_id": "SRC_JP_NIID",
            "local_name": "bacterial  dysentery",
            "disease_id": "D024",
            "metadata": {},
        },
        {
            "id": 2,
            "country_code": "JP",
            "source_id": "SRC_JP_NIID",
            "local_name": "Removed registry alias",
            "disease_id": "D024",
            "metadata": {"origin": "configs/mapping/jp.csv"},
        },
        {
            "id": 3,
            "country_code": "JP",
            "source_id": "*",
            "local_name": "Unrelated manual alias",
            "disease_id": "D024",
            "metadata": {},
        },
        {
            "id": 4,
            "country_code": "JP",
            "source_id": "*",
            "local_name": "Bacterial dysentery",
            "disease_id": "D024",
            "metadata": {},
        },
    ]

    assert _mapping_rows_to_deactivate(existing, incoming) == [1, 2, 4]
    plan = _mapping_deactivation_plan(existing, incoming)
    assert [item["reason"] for item in plan] == [
        "target_conflict",
        "removed_from_registry",
        "wildcard_shadow_conflict",
    ]
    assert plan[0]["replacement_candidates"] == [
        {
            "source_id": "SRC_JP_NIID",
            "disease_id": "D105",
            "local_name": "Bacterial dysentery",
        }
    ]


def test_mapping_scope_rekey_deactivates_old_registry_owned_jurisdiction() -> None:
    incoming = [
        {
            "country_code": "CA-ON",
            "source_id": "SRC_CA_ON_PHO_IDTO",
            "local_name": "Measles",
            "disease_id": "D017",
        }
    ]
    existing = [
        {
            "id": 17,
            "country_code": "CA",
            "source_id": "SRC_CA_ON_PHO_IDTO",
            "local_name": "Measles",
            "disease_id": "D017",
            "metadata": {"origin": "configs/mapping/ca-on.csv"},
        }
    ]

    plan = _mapping_deactivation_plan(existing, incoming)

    assert [item["mapping_id"] for item in plan] == [17]
    assert plan[0]["reason"] == "removed_from_registry"


def test_transition_manifest_covers_remap_and_component_reingestion() -> None:
    assert len(MAPPING_TRANSITIONS) >= 64
    actions = {item.action for item in MAPPING_TRANSITIONS}

    assert actions == {"remap_legacy", "remap_and_reingest", "source_reingest"}
    assert any(
        item.country_code == "JP" and item.old_id == "D062" and item.new_id == "D171"
        for item in MAPPING_TRANSITIONS
    )
    assert any(
        item.country_code == "US"
        and item.old_id == "D008"
        and item.new_id == "D208"
        and item.action == "source_reingest"
        for item in MAPPING_TRANSITIONS
    )
    assert any(
        item.country_code == "BR"
        and item.local_name == "Trachoma survey positive cases"
        and item.action == "source_reingest"
        for item in MAPPING_TRANSITIONS
    )


def test_exact_fact_evidence_rejects_aggregate_or_missing_component_value() -> None:
    remap = next(
        item
        for item in FACT_REMAPS
        if item.country_code == "TW" and item.old_id == "D024" and item.new_id == "D105"
    )

    assert (
        _fact_evidence_errors(
            {
                "cases": 0,
                "data_source": "Taiwan, China CDC NIDSS Open Data",
                "raw_data": {"RawDiseaseLabel": "桿菌性痢疾", "Cases": "0"},
            },
            remap,
        )
        == []
    )
    assert (
        "raw_component_count=2"
        in _fact_evidence_errors(
            {
                "cases": 2,
                "data_source": "Taiwan, China CDC NIDSS Open Data",
                "raw_data": [
                    {"RawDiseaseLabel": "桿菌性痢疾", "Cases": "1"},
                    {"RawDiseaseLabel": "其他", "Cases": "1"},
                ],
            },
            remap,
        )[0]
    )
    assert _fact_evidence_errors(
        {
            "cases": 0,
            "data_source": "Taiwan, China CDC NIDSS Open Data",
            "raw_data": {"RawDiseaseLabel": "桿菌性痢疾", "Cases": None},
        },
        remap,
    ) == ["raw component has no recognized observation value"]


def test_fact_remap_requires_exact_registry_source_evidence() -> None:
    remap = next(
        item
        for item in FACT_REMAPS
        if item.country_code == "TW" and item.old_id == "D024" and item.new_id == "D105"
    )

    assert _source_evidence_values(remap.source_id) == (
        "taiwan, china cdc nidss open data",
    )
    assert "old_record.data_source" in _fact_filter_sql(remap)
    assert "old_record.data_source" not in _fact_filter_sql(remap, include_source=False)
    assert _fact_evidence_errors(
        {
            "cases": 0,
            "data_source": "A different Taiwan source",
            "raw_data": {"RawDiseaseLabel": "桿菌性痢疾", "Cases": "0"},
        },
        remap,
    ) == [
        "data_source='A different Taiwan source' does not match Registry source "
        "SRC_TW_NIDSS"
    ]


def test_mapping_target_change_requires_a_declared_transition() -> None:
    changes = _mapping_target_changes(
        [
            {
                "id": 7,
                "country_code": "ZZ",
                "source_id": "SRC_ZZ",
                "local_name": "Example condition",
                "disease_id": "D001",
            }
        ],
        [
            {
                "country_code": "ZZ",
                "source_id": "SRC_ZZ",
                "local_name": "Example condition",
                "disease_id": "D002",
            }
        ],
    )

    assert changes == [
        {
            "existing_mapping_id": 7,
            "country_code": "ZZ",
            "existing_source_id": "SRC_ZZ",
            "source_id": "SRC_ZZ",
            "local_name": "Example condition",
            "old_disease_id": "D001",
            "new_disease_id": "D002",
            "declared": False,
            "migration_action": None,
        }
    ]
