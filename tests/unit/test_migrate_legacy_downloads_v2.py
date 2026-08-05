from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.migrate_legacy_downloads_v2 import migrate_legacy_downloads
from src.generation.github_data_snapshot import validate_github_snapshot
from src.generation.sharded_data_package import validate_sharded_data_package


def _legacy_tree(tmp_path: Path, *, relative_path: str = "countries/us.json") -> Path:
    root = tmp_path / "legacy"
    (root / "countries").mkdir(parents=True)
    row = {
        "dataset_kind": "country",
        "dataset_id": "us",
        "country_code": "US",
        "country_name": "United States",
        "disease_id": "D162",
        "disease_name_en": "HIV infection",
        "date": "2025-01-01",
        "cases": 3,
        "weekly_equiv_cases": 3.0,
        "deaths": 0,
        "incidence_rate_per_100k": None,
        "incidence_rate_source": None,
        "mortality_rate": None,
        "data_layer": "series_registry",
        "projection_policy": "representative_series",
        "series_codes": "SER_US_HIV",
        "loss_risk": None,
        "coverage_status": "parity",
        "legacy_gap_fill_count": 0,
        "coverage_ratio_against_legacy": 1.0,
        "primary_source_scope": "nhss",
        "source_scopes": "nhss",
    }
    (root / "countries" / "us.json").write_text(
        json.dumps({"metadata": {}, "records": [row]}),
        encoding="utf-8",
    )
    source_info = {
        "country_code": "US",
        "primary_scope": "nhss",
        "parser_primary": "us_nhss",
        "sources": [
            {
                "scope": "nhss",
                "label": "US CDC NHSS",
                "url": "https://example.test/nhss",
                "type": "csv",
            }
        ],
    }
    manifest = {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "countries": [
            {
                "kind": "country",
                "id": "us",
                "code": "US",
                "name_en": "United States",
                "name_zh": "美国",
                "record_count": 1,
                "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
                "relative_json_path": relative_path,
                "json_path": "https://example.test/countries/us.json",
                "csv_path": "https://example.test/countries/us.csv",
                "site_json_path": "/site-data/countries/us.json",
                "source_info": source_info,
            }
        ],
        "diseases": [
            {
                "kind": "disease",
                "id": "d162",
                "disease_id": "D162",
                "slug": "hiv-infection",
                "name_en": "HIV infection",
                "name_zh": "人类免疫缺乏病毒感染",
                "record_count": 1,
                "country_count": 1,
                "json_path": "https://example.test/diseases/d162.json",
                "csv_path": "https://example.test/diseases/d162.csv",
                "site_json_path": "/site-data/diseases/d162.json",
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_migration_builds_valid_package_and_github_snapshot(tmp_path: Path) -> None:
    legacy = _legacy_tree(tmp_path)
    output = tmp_path / "v2"
    snapshot = tmp_path / "github-snapshot"

    summary = migrate_legacy_downloads(
        legacy,
        output,
        github_snapshot_output=snapshot,
    )

    assert summary["legacy_record_count"] == 1
    assert validate_sharded_data_package(output).record_count == 1
    assert validate_github_snapshot(snapshot).release_count == 1
    assert summary["release_id"].startswith("20260801T000000Z-")


def test_migration_rejects_legacy_path_traversal(tmp_path: Path) -> None:
    legacy = _legacy_tree(tmp_path, relative_path="../outside.json")

    with pytest.raises(RuntimeError, match="Unsafe legacy download path"):
        migrate_legacy_downloads(
            legacy,
            tmp_path / "v2",
            github_snapshot_output=None,
        )
