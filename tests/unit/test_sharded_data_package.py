from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from src.generation.sharded_data_package import (
    PackageBuildError,
    PackageValidationError,
    UnsafePackagePathError,
    build_canonical_facts_release,
    build_sharded_data_package,
    validate_sharded_data_package,
)


METADATA = {
    "schema": {"name": "globalid.test-fact", "version": "1"},
    "release": {"id": "release-2026-08-04"},
    "dataset": {"id": "test-facts"},
}


def _read_shard(package_dir: Path, relative_path: str) -> list[dict]:
    with gzip.open(package_dir / relative_path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _canonical_records() -> list[dict]:
    return [
        {
            "date": "2024-01-13",
            "country_code": "US",
            "disease_id": "D210",
            "cases": 17,
            "source_refs": ["SRC_US_NNDSS"],
        },
        {
            "date": "2023-12-30",
            "country_code": "US",
            "disease_id": "D210",
            "cases": 12,
            "source_refs": ["SRC_US_NNDSS"],
        },
        {
            "date": "2024-01-27",
            "country_code": "US",
            "disease_id": "D210",
            "cases": 19,
            "source_refs": ["SRC_US_NNDSS"],
        },
        {
            "date": "2024-02-01",
            "country_code": "AU",
            "disease_id": "D210",
            "cases": 4,
            "source_refs": ["SRC_AU_NINDSS"],
        },
        {
            "date": "2024-01-20",
            "country_code": "US",
            "disease_id": "D004",
            "cases": 3,
            "source_refs": ["SRC_US_NNDSS"],
        },
    ]


def test_generic_package_partitions_by_year_and_uncompressed_byte_limit(
    tmp_path: Path,
) -> None:
    records = [
        {"date": "2024-02-01", "id": "c", "payload": "x" * 35},
        {"date": "2023-01-01", "id": "a", "payload": "x" * 35},
        {"date": "2024-01-01", "id": "b", "payload": "x" * 35},
        {"date": "2024-03-01", "id": "d", "payload": "x" * 35},
    ]
    output = tmp_path / "year-package"

    manifest = build_sharded_data_package(
        records,
        output,
        date_field="date",
        max_uncompressed_bytes=100,
        **METADATA,
    )
    validated = validate_sharded_data_package(output)

    assert manifest["manifest_version"] == 2
    assert manifest["package_mode"] == "year_partitioned"
    assert validated.record_count == 4
    assert {item["year"] for item in manifest["shards"]} == {2023, 2024}
    assert all(item["uncompressed_bytes"] <= 100 for item in manifest["shards"])
    assert [
        record["id"]
        for shard in manifest["shards"]
        for record in _read_shard(output, shard["path"])
    ] == ["a", "b", "c", "d"]


def test_canonical_facts_are_written_once_with_shared_country_and_disease_indexes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "canonical-release"
    records = _canonical_records()

    manifest = build_canonical_facts_release(
        reversed(records),
        output,
        date_field="date",
        country_metadata={"US": {"name": "United States"}},
        disease_metadata={
            "D210": {"name": "HIV/AIDS"},
            "D999": {"name": "No exported facts"},
        },
        source_catalog={"SRC_US_NNDSS": {"label": "US CDC NNDSS"}},
        max_uncompressed_bytes=180,
        **METADATA,
    )
    validated = validate_sharded_data_package(output)

    assert manifest["package_mode"] == "canonical_facts"
    assert validated.record_count == len(records)
    assert manifest["dataset"]["country_field"] == "country_code"
    assert manifest["dataset"]["disease_field"] == "disease_id"
    assert all(
        shard["path"]
        == f"objects/sha256/{shard['sha256'][:2]}/{shard['sha256']}.ndjson.gz"
        for shard in manifest["shards"]
    )
    us_d210_2024_parts = [
        shard["part"]
        for shard in manifest["shards"]
        if shard["country_code"] == "US"
        and shard["disease_id"] == "D210"
        and shard["year"] == 2024
    ]
    assert us_d210_2024_parts == [1, 2]

    us_descriptor = next(
        item
        for item in manifest["indexes"]["countries"]
        if item["country_code"] == "US"
    )
    d210_descriptor = next(
        item for item in manifest["indexes"]["diseases"] if item["disease_id"] == "D210"
    )
    us_index = json.loads((output / us_descriptor["path"]).read_text())
    d210_index = json.loads((output / d210_descriptor["path"]).read_text())
    expected_shared = {
        shard["path"]
        for shard in manifest["shards"]
        if shard["country_code"] == "US" and shard["disease_id"] == "D210"
    }

    assert set(us_index["shards"]) & set(d210_index["shards"]) == expected_shared
    assert us_index["metadata"] == {"name": "United States"}
    assert d210_index["metadata"] == {"name": "HIV/AIDS"}
    assert us_index["record_count"] == 4
    assert d210_index["record_count"] == 4
    empty_descriptor = next(
        item
        for item in manifest["indexes"]["diseases"]
        if item["disease_id"] == "D999"
    )
    empty_index = json.loads((output / empty_descriptor["path"]).read_text())
    assert empty_index["record_count"] == 0
    assert empty_index["date_start"] is None
    assert empty_index["shards"] == []
    assert not (output / "countries").exists()
    assert not (output / "diseases").exists()


def test_canonical_release_is_byte_stable_for_different_input_order(
    tmp_path: Path,
) -> None:
    records = _canonical_records()
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = build_canonical_facts_release(
        records,
        first,
        date_field="date",
        **METADATA,
    )
    second_manifest = build_canonical_facts_release(
        reversed(records),
        second,
        date_field="date",
        **METADATA,
    )

    assert (first / "manifest.json").read_bytes() == (
        second / "manifest.json"
    ).read_bytes()
    assert first_manifest == second_manifest
    us_d210_shards = [
        shard
        for shard in first_manifest["shards"]
        if shard["country_code"] == "US" and shard["disease_id"] == "D210"
    ]
    assert len(us_d210_shards) == 1
    assert us_d210_shards[0]["year"] is None
    for shard in first_manifest["shards"]:
        assert (first / shard["path"]).read_bytes() == (
            second / shard["path"]
        ).read_bytes()
    for kind in ("countries", "diseases"):
        for descriptor in first_manifest["indexes"][kind]:
            assert (first / descriptor["path"]).read_bytes() == (
                second / descriptor["path"]
            ).read_bytes()


def test_content_addressed_fact_objects_are_reused_across_release_metadata(
    tmp_path: Path,
) -> None:
    first = build_canonical_facts_release(
        _canonical_records(),
        tmp_path / "release-one",
        date_field="date",
        **METADATA,
    )
    second = build_canonical_facts_release(
        _canonical_records(),
        tmp_path / "release-two",
        date_field="date",
        schema=METADATA["schema"],
        release={"id": "release-2026-08-05"},
        dataset=METADATA["dataset"],
    )

    assert first["release"] != second["release"]
    assert [item["path"] for item in first["shards"]] == [
        item["path"] for item in second["shards"]
    ]
    assert [item["sha256"] for item in first["shards"]] == [
        item["sha256"] for item in second["shards"]
    ]


def test_validator_detects_content_and_manifest_tampering(tmp_path: Path) -> None:
    output = tmp_path / "tampered-release"
    manifest = build_canonical_facts_release(
        _canonical_records(),
        output,
        date_field="date",
        **METADATA,
    )
    first_path = output / manifest["shards"][0]["path"]
    tampered = bytearray(first_path.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    first_path.write_bytes(tampered)

    with pytest.raises(PackageValidationError, match="SHA-256 mismatch"):
        validate_sharded_data_package(output)

    manifest = build_canonical_facts_release(
        _canonical_records(),
        output,
        date_field="date",
        **METADATA,
    )
    index_path = output / manifest["indexes"]["countries"][0]["path"]
    index_path.write_bytes(index_path.read_bytes() + b" ")
    with pytest.raises(PackageValidationError, match="byte size mismatch"):
        validate_sharded_data_package(output)

    manifest = build_canonical_facts_release(
        _canonical_records(),
        output,
        date_field="date",
        **METADATA,
    )
    manifest["shards"][0]["path"] = "../outside.ndjson.gz"
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(UnsafePackagePathError, match="Unsafe shard path"):
        validate_sharded_data_package(output)


def test_atomic_replacement_removes_stale_package_files_and_preserves_old_on_error(
    tmp_path: Path,
) -> None:
    output = tmp_path / "replaceable-release"
    build_canonical_facts_release(
        _canonical_records(),
        output,
        date_field="date",
        **METADATA,
    )
    stale = output / "stale-object"
    stale.write_text("old", encoding="utf-8")

    replacement = [_canonical_records()[0]]
    build_canonical_facts_release(
        replacement,
        output,
        date_field="date",
        **METADATA,
    )
    assert not stale.exists()
    assert validate_sharded_data_package(output).record_count == 1
    before_failure = (output / "manifest.json").read_bytes()

    with pytest.raises(PackageBuildError, match="exceeding the per-shard limit"):
        build_canonical_facts_release(
            [
                {
                    "date": "2024-01-01",
                    "country_code": "US",
                    "disease_id": "D210",
                    "payload": "x" * 1000,
                }
            ],
            output,
            date_field="date",
            max_uncompressed_bytes=100,
            **METADATA,
        )

    assert (output / "manifest.json").read_bytes() == before_failure
    assert validate_sharded_data_package(output).record_count == 1


def test_builder_refuses_to_replace_an_unmarked_directory(tmp_path: Path) -> None:
    output = tmp_path / "ordinary-directory"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(UnsafePackagePathError, match="Refusing to replace"):
        build_sharded_data_package(
            [{"date": "2024-01-01", "value": 1}],
            output,
            date_field="date",
            **METADATA,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
