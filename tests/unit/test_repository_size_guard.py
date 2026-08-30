from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from scripts.check_repository_size import (
    TrackedBlob,
    find_violations,
    load_allowlist,
    main,
    revision_blobs,
    tracked_blobs,
)


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Repository Guard Test")
    _git(repo, "config", "user.email", "guard@example.invalid")
    return repo


def _track(repo: Path, relative_path: str, content: bytes) -> TrackedBlob:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _git(repo, "add", "-f", "--", relative_path)
    return next(blob for blob in tracked_blobs(repo) if blob.path == relative_path)


def test_regular_small_source_file_is_allowed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _track(repo, "src/example.py", b"print('ok')\n")

    assert find_violations(tracked_blobs(repo), allowlist=set(), max_blob_bytes=100) == []


def test_backup_directory_and_database_extensions_are_rejected(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _track(repo, "backups/report.csv", b"backup")
    _track(repo, "artifacts/snapshot.dump", b"dump")
    _track(repo, "tmp/postgres_backup_2026.sql", b"sql")

    violations = find_violations(
        tracked_blobs(repo), allowlist=set(), max_blob_bytes=1_000
    )

    assert {item.blob.path for item in violations} == {
        "artifacts/snapshot.dump",
        "backups/report.csv",
        "tmp/postgres_backup_2026.sql",
    }


def test_oversized_non_database_blob_is_rejected(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _track(repo, "assets/payload.bin", b"x" * 11)

    violations = find_violations(tracked_blobs(repo), allowlist=set(), max_blob_bytes=10)

    assert len(violations) == 1
    assert violations[0].reasons == ("blob exceeds 10 bytes",)


def test_allowlist_requires_exact_path_oid_and_size(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    original = _track(repo, "backups/legacy.dump", b"old")
    exact = {(original.path, original.oid, original.size)}
    assert find_violations(tracked_blobs(repo), allowlist=exact) == []

    replacement = _track(repo, "backups/legacy.dump", b"new")
    violations = find_violations(tracked_blobs(repo), allowlist=exact)

    assert replacement.oid != original.oid
    assert [item.blob for item in violations] == [replacement]


def test_baseline_validation_and_cli_failure(tmp_path: Path, capsys) -> None:
    repo = _repository(tmp_path)
    _track(repo, "data/backups/new.csv", b"new")
    baseline = repo / "baseline.json"
    baseline.write_text(
        json.dumps({"version": 1, "allowlist": []}), encoding="utf-8"
    )

    assert load_allowlist(baseline) == set()
    assert main(["--repo", str(repo), "--baseline", str(baseline)]) == 1
    assert "data/backups/new.csv" in capsys.readouterr().err


def test_revision_range_catches_large_blob_deleted_before_head(
    tmp_path: Path, capsys
) -> None:
    repo = _repository(tmp_path)
    _track(repo, "README.md", b"start")
    _git(repo, "commit", "--quiet", "-m", "initial")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _track(repo, "temporary.bin", b"x" * 20)
    _git(repo, "commit", "--quiet", "-m", "add large object")
    (repo / "temporary.bin").unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "--quiet", "-m", "remove large object")

    assert all(blob.path != "temporary.bin" for blob in tracked_blobs(repo))
    introduced = revision_blobs(repo, f"{base}..HEAD")
    violations = find_violations(introduced, allowlist=set(), max_blob_bytes=10)

    assert [item.blob.path for item in violations] == ["temporary.bin"]
    large_blob = violations[0].blob
    baseline = repo / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "allowlist": [
                    {
                        "path": large_blob.path,
                        "oid": large_blob.oid,
                        "size": large_blob.size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(
        [
            "--repo",
            str(repo),
            "--baseline",
            str(baseline),
            "--max-bytes",
            "10",
            "--revision-range",
            f"{base}..HEAD",
        ]
    ) == 1
    assert "temporary.bin" in capsys.readouterr().err


def test_legacy_backup_inventory_matches_current_tracked_files() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = json.loads(
        (root / "configs" / "legacy_backup_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    tracked = {
        line
        for line in _git(root, "ls-files", "backups", "data/backups")
        .stdout.splitlines()
        if line
    }
    declared = {entry["path"] for entry in inventory["files"]}

    assert inventory["external_storage_verified"] is False
    # These emergency backups are intentionally local-only and ignored by Git;
    # the manifest preserves their audit metadata without reintroducing binary
    # database artifacts into a clean clone.
    assert tracked == set()
    assert declared
    for entry in inventory["files"]:
        path = root / entry["path"]
        assert isinstance(entry["size"], int) and entry["size"] > 0
        assert len(entry["sha256"]) == 64
        if not path.exists():
            continue
        payload = path.read_bytes()
        assert len(payload) == entry["size"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
