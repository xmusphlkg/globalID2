from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_sharded_downloads import verify_package
from src.generation.sharded_data_package import build_canonical_facts_release


def _build_package(tmp_path: Path) -> Path:
    package = tmp_path / "v2"
    build_canonical_facts_release(
        [
            {
                "country_code": "US",
                "disease_id": "D162",
                "date": "2025-01-01",
                "cases": 4,
            },
            {
                "country_code": "BR",
                "disease_id": "D162",
                "date": "2025-02-01",
                "cases": 7,
            },
        ],
        package,
        date_field="date",
        schema={"id": "globalid.test"},
        release={"id": "test-release"},
        dataset={"id": "test-dataset"},
    )
    return package


def test_verify_package_checks_v2_indexes_and_legacy_parity(tmp_path: Path) -> None:
    package = _build_package(tmp_path)
    legacy_manifest = tmp_path / "manifest-v1.json"
    legacy_manifest.write_text(
        json.dumps(
            {
                "countries": [
                    {"id": "us", "record_count": 1},
                    {"id": "br", "record_count": 1},
                ],
                "diseases": [{"id": "d162", "record_count": 2}],
            }
        ),
        encoding="utf-8",
    )

    summary = verify_package(
        package,
        legacy_manifest=legacy_manifest,
        expected_records=2,
    )

    assert summary["status"] == "ok"
    assert summary["record_count"] == 2
    assert summary["index_record_totals"] == {
        "countries": 2,
        "diseases": 2,
    }
    assert summary["legacy_record_totals"] == {
        "countries": 2,
        "diseases": 2,
    }


def test_verify_package_rejects_legacy_total_mismatch(tmp_path: Path) -> None:
    package = _build_package(tmp_path)
    legacy_manifest = tmp_path / "manifest-v1.json"
    legacy_manifest.write_text(
        json.dumps(
            {
                "countries": [{"id": "us", "record_count": 1}],
                "diseases": [{"id": "d162", "record_count": 2}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Legacy countries total"):
        verify_package(package, legacy_manifest=legacy_manifest)
