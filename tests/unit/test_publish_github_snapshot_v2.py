from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import publish_github_snapshot_v2 as publisher
from src.generation.github_data_snapshot import build_github_snapshot
from src.generation.sharded_data_package import (
    PackageValidationError,
    build_canonical_facts_release,
)


def _snapshot(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    build_canonical_facts_release(
        [
            {
                "country_code": "US",
                "disease_id": "D162",
                "date": "2025-01-01",
                "cases": 1,
            }
        ],
        package,
        date_field="date",
        schema={"id": "globalid.test"},
        release={
            "release_id": "release-2026-08-05",
            "generated_at": "2026-08-05T00:00:00Z",
        },
        dataset={"id": "globalid.test"},
    )
    snapshot = tmp_path / "github-data-snapshot-v2"
    build_github_snapshot(package, snapshot)
    return snapshot


def test_dry_run_fully_validates_without_running_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(tmp_path)

    def unexpected_git(*args: object, **kwargs: object) -> str:
        raise AssertionError("dry-run must not run Git")

    monkeypatch.setattr(publisher, "run_git_command", unexpected_git)

    result = publisher.publish_github_snapshot_v2(snapshot)

    assert result.mode == "dry-run"
    assert result.branch == "snapshot-v2"
    assert result.latest_release_id == "release-2026-08-05"
    assert result.commit_oid is None


def test_invalid_snapshot_fails_before_any_git_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(tmp_path)
    manifest = json.loads(
        (snapshot / "releases/release-2026-08-05/manifest.json").read_text()
    )
    shard = snapshot / "releases/release-2026-08-05" / manifest["shards"][0]["path"]
    shard.write_bytes(shard.read_bytes() + b"tampered")
    calls: list[tuple[str, ...]] = []

    def record_git(args: list[str], cwd: Path, *, timeout_seconds: float) -> str:
        calls.append(tuple(args))
        return ""

    monkeypatch.setattr(publisher, "run_git_command", record_git)

    with pytest.raises(PackageValidationError, match="Compressed size mismatch"):
        publisher.publish_github_snapshot_v2(
            snapshot,
            repo_url="git@example.invalid:globalid/data.git",
            push=True,
        )

    assert calls == []


def test_legacy_csv_is_rejected_before_any_git_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _snapshot(tmp_path)
    legacy_csv = snapshot / "releases/release-2026-08-05/legacy.csv"
    legacy_csv.write_text("country,disease,cases\nUS,HIV,1\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def record_git(args: list[str], cwd: Path, *, timeout_seconds: float) -> str:
        calls.append(tuple(args))
        return ""

    monkeypatch.setattr(publisher, "run_git_command", record_git)

    with pytest.raises(publisher.SnapshotPublishError, match="Legacy CSV"):
        publisher.publish_github_snapshot_v2(
            snapshot,
            repo_url="git@example.invalid:globalid/data.git",
            push=True,
        )

    assert calls == []


@pytest.mark.parametrize("remote_oid", [None, "b" * 40])
def test_push_uses_orphan_snapshot_branch_and_exact_force_with_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_oid: str | None,
) -> None:
    snapshot = _snapshot(tmp_path)
    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_git(args: list[str], cwd: Path, *, timeout_seconds: float) -> str:
        calls.append((tuple(args), cwd))
        if args == ["rev-parse", "HEAD"]:
            return "a" * 40
        if args[:3] == ["ls-remote", "--heads", "--"]:
            if remote_oid is None:
                return ""
            return f"{remote_oid}\trefs/heads/snapshot-v2"
        return ""

    monkeypatch.setattr(publisher, "run_git_command", fake_git)

    result = publisher.publish_github_snapshot_v2(
        snapshot,
        repo_url="git@example.invalid:globalid/data.git",
        push=True,
        temporary_parent=tmp_path / "temporary",
    )

    arguments = [args for args, _cwd in calls]
    assert ("checkout", "--orphan", "snapshot-v2") in arguments
    assert arguments[-2] == (
        "ls-remote",
        "--heads",
        "--",
        "git@example.invalid:globalid/data.git",
        "refs/heads/snapshot-v2",
    )
    assert arguments[-1] == (
        "push",
        f"--force-with-lease=refs/heads/snapshot-v2:{remote_oid or ''}",
        "--",
        "git@example.invalid:globalid/data.git",
        "HEAD:refs/heads/snapshot-v2",
    )
    assert result.mode == "pushed"
    assert result.branch == "snapshot-v2"
    assert result.commit_oid == "a" * 40
    assert result.previous_remote_oid == remote_oid
    assert not calls[-1][1].exists()


def test_cli_is_dry_run_by_default_and_does_not_expose_branch_selection() -> None:
    args = publisher.parse_args([])

    assert args.push is False
    assert not hasattr(args, "branch")
