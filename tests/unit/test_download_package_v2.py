from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.generation.download_package_v2 import (
    build_frontend_download_manifest,
    build_globalid_canonical_download_package,
    build_globalid_download_package,
    build_source_catalog,
    canonicalize_country_download_row,
    release_id_from_generated_at,
)
from src.generation.sharded_data_package import (
    PackageBuildError,
    validate_sharded_data_package,
)


def _legacy_row(**overrides) -> dict:
    row = {
        "dataset_kind": "country",
        "dataset_id": "us",
        "dataset_slug": "us",
        "dataset_name": "United States",
        "country_code": "us",
        "country_name": "United States",
        "disease_id": "d162",
        "disease_name_en": "HIV infection",
        "disease_name_zh": "人类免疫缺乏病毒感染",
        "category": "Viral",
        "date": "2025-01-04",
        "year_month": "2025-01",
        "cases": 11,
        "weekly_equiv_cases": 11.0,
        "deaths": 0,
        "incidence_rate_per_100k": 0.01,
        "incidence_rate_source": "wpp_computed",
        "mortality_rate": None,
        "coverage_start": "2020-01-01",
        "coverage_end": "2025-01-04",
        "generated_at": "2026-08-01T00:00:00+00:00",
        "data_layer": "series_registry",
        "projection_policy": "representative_series",
        "series_codes": "SER_B|SER_A|SER_A",
        "loss_risk": None,
        "coverage_status": "parity",
        "legacy_gap_fill_count": 0,
        "coverage_ratio_against_legacy": 1.0,
        "primary_source_scope": "nndss_api",
        "primary_source_label": "US CDC NNDSS",
        "primary_source_url": "https://example.test",
        "primary_source_type": "api",
        "source_scopes": "nndss_api; nhss; nndss_api",
        "source_labels": "US CDC NNDSS|US CDC NHSS",
        "source_urls": "https://example.test|https://example.test/nhss",
        "source_types": "api|csv",
    }
    row.update(overrides)
    return row


def _source_info() -> dict[str, dict]:
    return {
        "US": {
            "country_code": "US",
            "primary_scope": "nndss_api",
            "parser_primary": "us_nndss",
            "sources": [
                {
                    "scope": "nndss_api",
                    "label": "US CDC NNDSS",
                    "url": "https://example.test",
                    "type": "api",
                    "cadence": "weekly",
                },
                {
                    "scope": "nhss",
                    "label": "US CDC NHSS",
                    "url": "https://example.test/nhss",
                    "type": "csv",
                    "cadence": "yearly",
                },
            ],
        }
    }


def _country_entries() -> list[dict]:
    return [
        {
            "id": "us",
            "code": "US",
            "name_en": "United States",
            "name_zh": "美国",
            "record_count": 1,
            "date_range": {"start": "2025-01-04", "end": "2025-01-04"},
            "json_path": "countries/us.json",
            "csv_path": "countries/us.csv",
            "site_json_path": "/site-data/countries/us.json",
        }
    ]


def _disease_entries() -> list[dict]:
    return [
        {
            "id": "d162",
            "disease_id": "D162",
            "slug": "hiv-infection",
            "name_en": "HIV infection",
            "name_zh": "人类免疫缺乏病毒感染",
            "record_count": 1,
            "country_count": 1,
            "json_path": "diseases/d162.json",
            "csv_path": "diseases/d162.csv",
            "site_json_path": "/site-data/diseases/d162.json",
        }
    ]


def test_country_row_normalization_removes_repeated_v1_metadata() -> None:
    fact = canonicalize_country_download_row(_legacy_row())

    assert fact["country_code"] == "US"
    assert fact["disease_id"] == "D162"
    assert fact["series_codes"] == ["SER_A", "SER_B"]
    assert fact["primary_source_ref"] == "US:nndss_api"
    assert fact["source_refs"] == ["US:nhss", "US:nndss_api"]
    assert fact["coverage_status"] == "parity"
    for removed in (
        "dataset_kind",
        "dataset_id",
        "country_name",
        "disease_name_en",
        "category",
        "year_month",
        "coverage_start",
        "coverage_end",
        "generated_at",
        "source_urls",
    ):
        assert removed not in fact


def test_source_catalog_is_deduplicated_and_marks_primary() -> None:
    catalog = build_source_catalog(_source_info())

    assert list(catalog) == ["US:nhss", "US:nndss_api"]
    assert catalog["US:nndss_api"]["is_primary"] is True
    assert catalog["US:nndss_api"]["parser"] == "us_nndss"
    assert catalog["US:nhss"]["is_primary"] is False


def test_globalid_package_builds_shared_validated_indexes(tmp_path: Path) -> None:
    output = tmp_path / "site-downloads-v2"
    manifest = build_globalid_download_package(
        [_legacy_row()],
        output,
        generated_at="2026-08-01T00:00:00+00:00",
        country_entries=_country_entries(),
        disease_entries=_disease_entries(),
        source_info_by_country=_source_info(),
    )
    result = validate_sharded_data_package(output)

    assert result.record_count == 1
    assert manifest["release"]["release_id"].startswith("20260801T000000Z-")
    assert len(manifest["release"]["content_sha256"]) == 64
    assert manifest["totals"]["record_count"] == 1
    country_index = json.loads(
        (output / manifest["indexes"]["countries"][0]["path"]).read_text()
    )
    disease_index = json.loads(
        (output / manifest["indexes"]["diseases"][0]["path"]).read_text()
    )
    assert country_index["shards"] == disease_index["shards"]
    assert country_index["metadata"]["site_json_path"] == (
        "/site-data/countries/us.json"
    )
    assert "legacy" not in country_index["metadata"]
    assert manifest["release"]["stage"] == "production"

    frontend = build_frontend_download_manifest(
        manifest,
        snapshot_url_base="https://raw.example/snapshot-v2",
        country_entries=_country_entries(),
        disease_entries=_disease_entries(),
    )
    assert frontend["manifest_version"] == 2
    assert frontend["record_count"] == 1
    assert frontend["countries"][0]["dataset_index_path"].endswith(
        f"/releases/{manifest['release']['release_id']}/indexes/countries/US.json"
    )
    assert "json_path" not in frontend["countries"][0]
    assert "source_info" not in frontend["diseases"][0]


def test_unknown_source_reference_fails_before_installing_package(
    tmp_path: Path,
) -> None:
    output = tmp_path / "site-downloads-v2"

    with pytest.raises(PackageBuildError, match="unknown source"):
        build_globalid_download_package(
            [_legacy_row(source_scopes="not-in-catalog")],
            output,
            generated_at="2026-08-01T00:00:00+00:00",
            country_entries=_country_entries(),
            disease_entries=_disease_entries(),
            source_info_by_country=_source_info(),
        )

    assert not output.exists()


def test_production_builder_accepts_only_canonical_fact_shape(tmp_path: Path) -> None:
    output = tmp_path / "canonical-v2"
    fact = canonicalize_country_download_row(_legacy_row())

    manifest = build_globalid_canonical_download_package(
        [fact],
        output,
        generated_at="2026-08-01T00:00:00+00:00",
        country_entries=_country_entries(),
        disease_entries=_disease_entries(),
        source_info_by_country=_source_info(),
    )

    assert manifest["totals"]["record_count"] == 1
    assert manifest["release"]["stage"] == "production"
    assert validate_sharded_data_package(output).record_count == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-01T00:00:00+00:00", "20260801T000000Z"),
        ("2026-08-01T08:00:00+08:00", "20260801T000000Z"),
        ("release candidate / 4", "release-candidate-4"),
    ],
)
def test_release_id_is_path_safe(value: str, expected: str) -> None:
    assert release_id_from_generated_at(value) == expected
