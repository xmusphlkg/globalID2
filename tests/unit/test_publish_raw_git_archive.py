from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import publish_raw_git_archive as archive
from scripts.verify_raw_git_archive import verify_raw_archive


def test_configure_github_ssh_transport_uses_port_443(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -i /tmp/release-key")

    configured = archive.configure_github_ssh_transport(
        "git@github.com:xmusphlkg/globalID-data-archive.git"
    )

    command = os.environ["GIT_SSH_COMMAND"]
    assert configured is True
    assert "-o BatchMode=yes" in command
    assert "-i /tmp/release-key" in command
    assert "Hostname=ssh.github.com" in command
    assert "-p 443" in command


def _git_log(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_local_archive_is_readable_incremental_and_excludes_auth(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    (source / "us").mkdir(parents=True)
    payload = source / "us" / "cases.json"
    payload.write_text('{"cases": 7}\n', encoding="utf-8")
    (source / "us" / "_auth").mkdir()
    (source / "us" / "_auth" / "token.json").write_text("secret", encoding="utf-8")

    first = archive.publish_raw_archive(source, repository)

    assert first.changed is True
    assert first.branch == "main"
    assert first.source_file_count == 1
    assert first.added_file_count == 1
    assert (repository / "data/raw/us/cases.json").read_bytes() == payload.read_bytes()
    assert not any("_auth" in path.parts for path in (repository / "data/raw").rglob("*"))
    assert not (repository / "objects").exists()
    assert not (repository / "manifests").exists()

    restored = tmp_path / "restored"
    verified = verify_raw_archive(repository, source_dir=source, restore_dir=restored)
    assert verified.file_count == 1
    assert verified.direct_file_count == 1
    assert (restored / "us/cases.json").read_bytes() == payload.read_bytes()

    initial_log = _git_log(repository)
    second = archive.publish_raw_archive(source, repository)

    assert second.changed is False
    assert second.commit_count == 0
    assert _git_log(repository) == initial_log


def test_changed_and_removed_files_update_the_main_mirror(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    payload = source / "payload.bin"
    removed = source / "removed.txt"
    payload.write_bytes(b"old payload")
    removed.write_text("old", encoding="utf-8")
    archive.publish_raw_archive(source, repository)

    payload.write_bytes(b"new payload")
    removed.unlink()
    result = archive.publish_raw_archive(source, repository)

    assert result.changed is True
    assert result.updated_file_count == 1
    assert result.removed_storage_path_count == 1
    assert (repository / "data/raw/payload.bin").read_bytes() == b"new payload"
    assert not (repository / "data/raw/removed.txt").exists()
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "main"


def test_large_file_uses_adjacent_raw_parts_and_can_be_restored(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(archive, "MAX_DIRECT_FILE_BYTES", 10)
    monkeypatch.setattr(archive, "PART_BYTES", 8)
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    payload = source / "large.dbc"
    payload.write_bytes(b"abcdefghijklmnopqrstuvwxyz")

    result = archive.publish_raw_archive(source, repository)

    assert result.split_file_count == 1
    assert not (repository / "data/raw/large.dbc").exists()
    parts = sorted((repository / "data/raw/large.dbc.parts").glob("part-*"))
    assert len(parts) == 4
    assert b"".join(path.read_bytes() for path in parts) == payload.read_bytes()
    index = json.loads((repository / archive.LARGE_FILES_INDEX).read_text(encoding="utf-8"))
    assert index["files"][0]["path"] == "large.dbc"

    restored = tmp_path / "restored"
    verified = verify_raw_archive(repository, source_dir=source, restore_dir=restored)
    assert verified.split_file_count == 1
    assert (restored / "large.dbc").read_bytes() == payload.read_bytes()


def test_interrupted_uncommitted_file_is_committed_on_next_run(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    (source / "first.bin").write_bytes(b"first")
    archive.publish_raw_archive(source, repository)

    (source / "second.bin").write_bytes(b"second")
    destination = repository / "data/raw/second.bin"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"second")

    result = archive.publish_raw_archive(source, repository)

    assert result.changed is True
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "data/raw/second.bin"],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    assert tracked.returncode == 0


def test_interrupted_unstaged_deletion_is_committed_on_next_run(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    repository = tmp_path / "archive"
    source.mkdir()
    source_file = source / "removed.bin"
    source_file.write_bytes(b"remove me")
    archive.publish_raw_archive(source, repository)
    source_file.unlink()
    (repository / "data/raw/removed.bin").unlink()

    result = archive.publish_raw_archive(source, repository)

    assert result.changed is True
    assert result.removed_storage_path_count == 1
    assert subprocess.run(
        ["git", "ls-files", "--error-unmatch", "data/raw/removed.bin"],
        cwd=repository,
        capture_output=True,
        check=False,
    ).returncode != 0


def test_existing_raw_v1_clone_switches_and_pushes_directly_to_main(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    repository = tmp_path / "archive"
    source = tmp_path / "raw"
    subprocess.run(["git", "init", "--bare", "--quiet", remote], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", seed], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=seed, check=True)
    (seed / "README.md").write_text("old design\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "docs"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "--quiet", "origin", "main"], cwd=seed, check=True)
    subprocess.run(["git", "checkout", "--quiet", "-b", "raw-v1"], cwd=seed, check=True)
    (seed / "legacy-object").write_text("legacy\n", encoding="utf-8")
    subprocess.run(["git", "add", "legacy-object"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "legacy"], cwd=seed, check=True)
    subprocess.run(["git", "push", "--quiet", "origin", "raw-v1"], cwd=seed, check=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--single-branch", "--branch", "raw-v1", remote, repository],
        check=True,
    )
    source.mkdir()
    (source / "new.json").write_text('{"new": true}\n', encoding="utf-8")

    result = archive.publish_raw_archive(source, repository, repo_url=str(remote), push=True)

    assert result.branch == "main"
    assert result.mode == "pushed"
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "main"
    main_tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "data/raw/new.json" in main_tree
    raw_v1_tree = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "raw-v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "legacy-object" in raw_v1_tree
    assert "data/raw/new.json" not in raw_v1_tree


def test_existing_unpushed_commit_is_resumed_before_sync(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    repository = tmp_path / "archive"
    subprocess.run(["git", "init", "--bare", "--quiet", remote], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", seed], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=seed, check=True)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "--quiet", "origin", "main"], cwd=seed, check=True)
    subprocess.run(["git", "clone", "--quiet", "--branch", "main", remote, repository], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    (repository / "pending.txt").write_text("resume me\n", encoding="utf-8")
    subprocess.run(["git", "add", "pending.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "pending"], cwd=repository, check=True)

    archive._ensure_repository(
        repository,
        repo_url=str(remote),
        push=True,
        timeout_seconds=30,
    )

    local_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_head == local_head


def test_push_branch_retries_transient_tls_failure(monkeypatch, tmp_path: Path) -> None:
    attempts = []
    delays = []

    def flaky_run_git(args, cwd, **kwargs):
        attempts.append((args, cwd, kwargs))
        if len(attempts) < 3:
            raise archive.RawArchiveError(
                "git push failed: GnuTLS, handshake failed: "
                "The TLS connection was non-properly terminated"
            )
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(archive, "run_git", flaky_run_git)
    monkeypatch.setattr(archive.time, "sleep", delays.append)

    archive._push_branch(
        tmp_path,
        timeout_seconds=30,
        attempts=4,
        retry_delay_seconds=0.5,
    )

    assert len(attempts) == 3
    assert delays == [0.5, 1.0]
    assert attempts[0][0] == ["push", "origin", "HEAD:refs/heads/main"]


def test_push_branch_does_not_retry_permanent_git_failure(
    monkeypatch, tmp_path: Path
) -> None:
    attempts = []
    delays = []

    def rejected_run_git(args, cwd, **kwargs):
        attempts.append((args, cwd, kwargs))
        raise archive.RawArchiveError(
            "git push failed: remote rejected (non-fast-forward)"
        )

    monkeypatch.setattr(archive, "run_git", rejected_run_git)
    monkeypatch.setattr(archive.time, "sleep", delays.append)

    with pytest.raises(archive.RawArchiveError, match="non-fast-forward"):
        archive._push_branch(tmp_path, timeout_seconds=30)

    assert len(attempts) == 1
    assert delays == []


def test_obsolete_worker_arguments_are_accepted_during_service_transition() -> None:
    args = archive.parse_args(
        [
            "--chunk-mib",
            "48",
            "--commit-batch-mib",
            "384",
            "--zstd-level",
            "6",
        ]
    )

    assert args.chunk_mib == 48
    assert args.commit_batch_mib == 384
    assert args.zstd_level == 6
