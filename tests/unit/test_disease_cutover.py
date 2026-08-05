from __future__ import annotations

import json

import pytest

from src.core.disease_cutover import (
    DiseaseCutoverConfig,
    DiseaseCutoverConfigError,
    load_disease_cutover_config,
)


def _document() -> dict:
    return {
        "schema_version": 1,
        "release_version": "test-1",
        "defaults": {
            "read_mode": "series_with_fallback",
            "shadow_compare": True,
            "legacy_write_mode": "dual",
        },
        "targets": [],
        "sources": [],
    }


def test_repository_default_is_safe_and_resolves_unknown_targets() -> None:
    config = load_disease_cutover_config()

    read = config.resolve_read_policy("us", "d210")
    write = config.resolve_write_policy("src_us_nndss")

    assert read.read_mode == "series_with_fallback"
    assert read.shadow_compare is True
    assert read.may_query_legacy is True
    assert read.target_override is False
    assert write.legacy_write_mode == "dual"
    assert write.writes_legacy is True
    assert write.source_override is False


def test_country_concept_target_can_enter_series_only() -> None:
    document = _document()
    document["targets"] = [
        {
            "country_code": "US",
            "concept_id": "D210",
            "read_mode": "series_only",
            "shadow_compare": False,
            "required_series": [
                "SER_US_ACUTE_HEPATITIS_C_CONFIRMED",
                "SER_US_ACUTE_HEPATITIS_C_PROBABLE",
            ],
            "allowed_projection_policy": "sum_disjoint",
            "approval": {
                "approved_by": "data-owner",
                "approved_at": "2026-08-04",
            },
        }
    ]

    policy = DiseaseCutoverConfig.from_mapping(document).read_policy("us", "d210")

    assert policy.read_mode == "series_only"
    assert policy.shadow_compare is False
    assert policy.may_query_legacy is False
    assert policy.requires_series is True
    assert policy.target_override is True
    assert policy.required_series == (
        "SER_US_ACUTE_HEPATITIS_C_CONFIRMED",
        "SER_US_ACUTE_HEPATITIS_C_PROBABLE",
    )
    assert policy.allowed_projection_policy == "sum_disjoint"


def test_target_override_can_inherit_safe_default_read_mode() -> None:
    document = _document()
    document["targets"] = [
        {
            "country_code": "JP",
            "concept_id": "D005",
            "shadow_compare": False,
        }
    ]

    policy = DiseaseCutoverConfig.from_mapping(document).read_policy("JP", "D005")

    assert policy.read_mode == "series_with_fallback"
    assert policy.shadow_compare is False


def test_series_only_is_forbidden_as_global_default() -> None:
    document = _document()
    document["defaults"]["read_mode"] = "series_only"

    with pytest.raises(DiseaseCutoverConfigError, match="only for an explicit"):
        DiseaseCutoverConfig.from_mapping(document)


def test_series_only_target_requires_series_policy_and_approval() -> None:
    document = _document()
    document["targets"] = [
        {
            "country_code": "US",
            "concept_id": "D210",
            "read_mode": "series_only",
        }
    ]

    with pytest.raises(DiseaseCutoverConfigError, match="approval is required"):
        DiseaseCutoverConfig.from_mapping(document)


@pytest.mark.parametrize("write_mode", ["compare_only", "off"])
def test_non_dual_source_override_requires_checkpoint(write_mode: str) -> None:
    document = _document()
    document["sources"] = [
        {
            "source_id": "SRC_US_NNDSS",
            "legacy_write_mode": write_mode,
            "approval": {
                "approved_by": "data-owner",
                "approved_at": "2026-08-04",
            },
        }
    ]

    with pytest.raises(
        DiseaseCutoverConfigError, match="no source_partition_checkpoint"
    ):
        DiseaseCutoverConfig.from_mapping(document)


@pytest.mark.parametrize("write_mode", ["compare_only", "off"])
def test_non_dual_source_override_requires_approval(write_mode: str) -> None:
    document = _document()
    document["sources"] = [
        {
            "source_id": "SRC_US_NNDSS",
            "legacy_write_mode": write_mode,
            "source_partition_checkpoint": "SRC_US_NNDSS:country:US:national",
        }
    ]

    with pytest.raises(DiseaseCutoverConfigError, match="approval is required"):
        DiseaseCutoverConfig.from_mapping(document)


@pytest.mark.parametrize("write_mode", ["compare_only", "off"])
def test_approved_source_can_disable_legacy_writes(write_mode: str) -> None:
    document = _document()
    document["sources"] = [
        {
            "source_id": "SRC_US_NNDSS",
            "legacy_write_mode": write_mode,
            "source_partition_checkpoint": {
                "partition_key": "SRC_US_NNDSS:country:US:national",
                "verified_at": "2026-08-04T12:00:00Z",
            },
            "approval": {
                "approved_by": "data-owner",
                "approved_at": "2026-08-04T12:05:00Z",
                "reason": "Series ingestion checkpoint is authoritative",
                "change_ref": "CUTOVER-42",
            },
        }
    ]

    policy = DiseaseCutoverConfig.from_mapping(document).write_policy("src_us_nndss")

    assert policy.legacy_write_mode == write_mode
    assert policy.writes_legacy is False
    assert policy.builds_legacy_comparison is (write_mode == "compare_only")
    assert policy.source_override is True
    assert policy.approval is not None
    assert policy.approval.change_ref == "CUTOVER-42"


def test_global_non_dual_write_default_is_forbidden() -> None:
    document = _document()
    document["defaults"]["legacy_write_mode"] = "off"

    with pytest.raises(DiseaseCutoverConfigError, match="must remain dual"):
        DiseaseCutoverConfig.from_mapping(document)


def test_duplicate_normalized_target_is_rejected() -> None:
    document = _document()
    document["targets"] = [
        {"country_code": "US", "concept_id": "D210", "read_mode": "legacy"},
        {"country_code": "us", "concept_id": "d210", "read_mode": "legacy"},
    ]

    with pytest.raises(DiseaseCutoverConfigError, match="duplicate.*US/D210"):
        DiseaseCutoverConfig.from_mapping(document)


def test_load_reports_invalid_json_path(tmp_path) -> None:
    path = tmp_path / "disease_cutover.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(DiseaseCutoverConfigError, match="invalid disease cutover JSON"):
        load_disease_cutover_config(path)


def test_round_trip_from_json_file(tmp_path) -> None:
    path = tmp_path / "disease_cutover.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    config = load_disease_cutover_config(path)

    assert config.release_version == "test-1"
