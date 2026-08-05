from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.generation.github_data_snapshot import (
    GitHubSnapshotError,
    build_github_snapshot,
    validate_github_snapshot,
)
from src.generation.sharded_data_package import (
    PackageValidationError,
    UnsafePackagePathError,
    build_canonical_facts_release,
)


def _package(
    tmp_path: Path,
    release_id: str,
    *,
    cases: int,
    generated_at: str,
) -> Path:
    output = tmp_path / f"package-{release_id}"
    build_canonical_facts_release(
        [
            {
                "country_code": "US",
                "disease_id": "D162",
                "date": "2025-01-01",
                "cases": cases,
            }
        ],
        output,
        date_field="date",
        schema={"id": "globalid.test"},
        release={"release_id": release_id, "generated_at": generated_at},
        dataset={"id": "globalid.test"},
    )
    return output


def test_snapshot_retains_bounded_releases_and_points_to_latest(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "github-tree"
    releases = [
        _package(
            tmp_path,
            f"r{number}",
            cases=number,
            generated_at=f"2026-08-0{number}T00:00:00Z",
        )
        for number in range(1, 5)
    ]

    for package in releases:
        build_github_snapshot(package, snapshot, retain_releases=3)

    result = validate_github_snapshot(snapshot)
    latest = json.loads((snapshot / "latest.json").read_text())
    retained = sorted(path.name for path in (snapshot / "releases").iterdir())

    assert result.latest_release_id == "r4"
    assert result.release_count == 3
    assert latest["manifest_path"] == "releases/r4/manifest.json"
    assert retained == ["r2", "r3", "r4"]
    assert not (snapshot / "releases" / "r1").exists()


def test_snapshot_rejects_mutating_an_existing_release_and_preserves_tree(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "github-tree"
    first = _package(
        tmp_path,
        "stable",
        cases=1,
        generated_at="2026-08-01T00:00:00Z",
    )
    conflicting = _package(
        tmp_path,
        "stable-copy",
        cases=2,
        generated_at="2026-08-01T00:00:00Z",
    )
    conflicting_manifest_path = conflicting / "manifest.json"
    conflicting_manifest = json.loads(conflicting_manifest_path.read_text())
    conflicting_manifest["release"]["release_id"] = "stable"
    conflicting_manifest_path.write_text(
        json.dumps(
            conflicting_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    build_github_snapshot(first, snapshot)
    before = (snapshot / "latest.json").read_bytes()

    with pytest.raises(GitHubSnapshotError, match="different bytes"):
        build_github_snapshot(conflicting, snapshot)

    assert (snapshot / "latest.json").read_bytes() == before
    assert validate_github_snapshot(snapshot).latest_release_id == "stable"


def test_snapshot_validator_detects_tampered_release_object(tmp_path: Path) -> None:
    snapshot = tmp_path / "github-tree"
    package = _package(
        tmp_path,
        "tamper-test",
        cases=1,
        generated_at="2026-08-01T00:00:00Z",
    )
    build_github_snapshot(package, snapshot)
    manifest = json.loads(
        (snapshot / "releases" / "tamper-test" / "manifest.json").read_text()
    )
    shard_path = snapshot / "releases" / "tamper-test" / manifest["shards"][0]["path"]
    shard_path.write_bytes(shard_path.read_bytes() + b"tampered")

    with pytest.raises(PackageValidationError, match="Compressed size mismatch"):
        validate_github_snapshot(snapshot)


def test_snapshot_size_guard_fails_before_replacing_previous_tree(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "github-tree"
    first = _package(
        tmp_path,
        "r1",
        cases=1,
        generated_at="2026-08-01T00:00:00Z",
    )
    second = _package(
        tmp_path,
        "r2",
        cases=2,
        generated_at="2026-08-02T00:00:00Z",
    )
    build_github_snapshot(first, snapshot)

    with pytest.raises(GitHubSnapshotError, match="snapshot file exceeds"):
        build_github_snapshot(second, snapshot, max_file_bytes=64)

    assert validate_github_snapshot(snapshot).latest_release_id == "r1"


def test_snapshot_builder_refuses_unmarked_output_directory(tmp_path: Path) -> None:
    package = _package(
        tmp_path,
        "r1",
        cases=1,
        generated_at="2026-08-01T00:00:00Z",
    )
    output = tmp_path / "ordinary"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(UnsafePackagePathError, match="Refusing to replace"):
        build_github_snapshot(package, output)

    assert sentinel.read_text(encoding="utf-8") == "keep"
