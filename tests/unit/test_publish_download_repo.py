from __future__ import annotations

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from scripts.publish_download_repo import (
    configure_github_ssh_transport,
    ensure_commit_identity,
    ensure_repo,
    push_branch,
    push_download_snapshot,
    run_git,
    run_git_with_retry,
    summarize_git_status,
    sync_managed_assets,
    validate_source,
)


def _write_source(root, payload: bytes = b"current partition"):
    base = "https://raw.githubusercontent.com/example/data/main"
    files = {}
    for format_name in ("csv", "json", "xlsx"):
        relative = f"diseases/d007/2026-2029.{format_name}"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = payload + format_name.encode()
        path.write_bytes(content)
        files[format_name] = {
            "url": f"{base}/{relative}",
            "relative_path": relative,
            "filename": f"globalid-d007-2026-2029.{format_name}",
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    (root / "countries").mkdir(exist_ok=True)
    manifest = {
        "schema_version": 4,
        "formats": ["csv", "json", "xlsx"],
        "download_url_base": base,
        "countries": [],
        "diseases": [
            {
                "id": "d007",
                "disease_id": "D007",
                "parts": [{"id": "2026-2029", "is_current": True, "files": files}],
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_validate_source_accepts_partitioned_three_format_manifest(tmp_path):
    _write_source(tmp_path)
    manifest = validate_source(tmp_path, "main")
    assert manifest["diseases"][0]["parts"][0]["id"] == "2026-2029"


def test_validate_source_rejects_assets_not_declared_by_manifest(tmp_path):
    _write_source(tmp_path)
    unexpected = tmp_path / "diseases" / "d007" / "unexpected.csv"
    unexpected.write_text("unexpected")

    with pytest.raises(RuntimeError, match="not declared by manifest"):
        validate_source(tmp_path, "main")


def test_validate_source_rejects_unsafe_manifest_asset_path(tmp_path):
    manifest = _write_source(tmp_path)
    manifest["diseases"][0]["parts"][0]["files"]["csv"]["relative_path"] = "../outside.csv"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(RuntimeError, match="Unsafe manifest asset path"):
        validate_source(tmp_path, "main")


def test_incremental_sync_copies_only_changed_partition(tmp_path):
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    _write_source(source)

    first = sync_managed_assets(source, checkout)
    second = sync_managed_assets(source, checkout)
    assert first["copied"] == 4
    assert second == {"copied": 0, "removed": 0}

    _write_source(source, payload=b"revised current partition")
    third = sync_managed_assets(source, checkout)
    assert third["copied"] == 4  # three current files plus manifest


def test_incremental_sync_does_not_compare_unchanged_asset_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    _write_source(source)
    sync_managed_assets(source, checkout)

    def manifest_only(source_path, target_path):
        assert source_path.name == target_path.name == "manifest.json"
        return True

    monkeypatch.setattr("scripts.publish_download_repo.files_match", manifest_only)
    assert sync_managed_assets(source, checkout) == {"copied": 0, "removed": 0}


def test_incremental_sync_restores_missing_file_and_checks_changed_content(tmp_path):
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    _write_source(source)
    sync_managed_assets(source, checkout)
    target = checkout / "diseases/d007/2026-2029.csv"
    target.unlink()
    assert sync_managed_assets(source, checkout) == {"copied": 1, "removed": 0}

    original = target.read_bytes()
    _write_source(source, payload=b"revised current partition")
    changed = source / "diseases/d007/2026-2029.csv"
    changed.write_bytes(b"x" * changed.stat().st_size)
    with pytest.raises(RuntimeError, match="Manifest checksum mismatch"):
        sync_managed_assets(source, checkout)
    assert target.read_bytes() == original


def test_ensure_repo_switches_a_stale_checkout_to_divergent_remote_branch(tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)

    def commit(filename: str, content: str, message: str) -> str:
        (seed / filename).write_text(content)
        subprocess.run(["git", "add", filename], cwd=seed, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release@example.test",
                "commit",
                "-m",
                message,
            ],
            cwd=seed,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=seed, check=True, text=True, capture_output=True
        ).stdout.strip()

    commit("base.txt", "base\n", "base")
    subprocess.run(["git", "branch", "-M", "main"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)
    main_head = commit("main.txt", "main\n", "main only")
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "master", "HEAD~1"], cwd=seed, check=True)
    master_head = commit("master.txt", "master\n", "master only")
    subprocess.run(["git", "push", "origin", "master"], cwd=seed, check=True, capture_output=True)

    subprocess.run(["git", "clone", "--branch", "main", str(remote), str(checkout)], check=True, capture_output=True)
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, check=True, text=True, capture_output=True
    ).stdout.strip() == main_head

    ensure_repo(str(remote), "master", checkout)

    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, check=True, text=True, capture_output=True
    ).stdout.strip() == master_head


def test_commit_identity_reuses_repository_author_for_fresh_worker(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init"], cwd=checkout, check=True, capture_output=True)
    (checkout / "README.md").write_text("data\n")
    subprocess.run(["git", "add", "README.md"], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Repository Author",
            "-c",
            "user.email=repository@example.test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)

    identity = ensure_commit_identity(checkout)

    assert identity == {
        "name": "Repository Author",
        "email": "repository@example.test",
        "source": "repository_history",
    }
    assert subprocess.run(
        ["git", "config", "--get", "user.name"],
        cwd=checkout,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "Repository Author"
    assert subprocess.run(
        ["git", "config", "--get", "user.email"],
        cwd=checkout,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "repository@example.test"


def test_run_git_surfaces_stderr_for_control_plane_diagnostics(tmp_path):
    with pytest.raises(RuntimeError, match=r"not a git repository"):
        run_git(["status"], tmp_path)


def test_git_status_summary_limits_displayed_paths():
    status = "\n".join(f" M diseases/d007/part-{index}.csv" for index in range(20))

    summary = summarize_git_status(status, max_paths=3)

    assert "Git changes ready: 20 paths (M: 20)" in summary
    assert "part-0.csv" in summary
    assert "part-2.csv" in summary
    assert "part-3.csv" not in summary
    assert "17 additional paths omitted" in summary


def test_push_branch_retries_transient_transport_failures(tmp_path, monkeypatch):
    attempts = []
    delays = []

    def flaky_run_git(args, cwd):
        attempts.append((args, cwd))
        if len(attempts) < 3:
            raise RuntimeError("ssh: connect to host github.com port 22: Connection timed out")
        return ""

    monkeypatch.setattr("scripts.publish_download_repo.run_git", flaky_run_git)
    monkeypatch.setattr("scripts.publish_download_repo.time.sleep", delays.append)

    push_branch(tmp_path, "main", attempts=3, retry_delay_seconds=0.25)

    assert attempts == [(["push", "origin", "main"], tmp_path)] * 3
    assert delays == [0.25, 0.5]


def test_git_network_retry_does_not_hide_permanent_failure(tmp_path, monkeypatch):
    attempts = []

    def rejected_run_git(args, cwd):
        attempts.append((args, cwd))
        raise RuntimeError("remote rejected: non-fast-forward")

    monkeypatch.setattr("scripts.publish_download_repo.run_git", rejected_run_git)

    with pytest.raises(RuntimeError, match="non-fast-forward"):
        run_git_with_retry(["fetch", "origin", "main"], tmp_path)

    assert len(attempts) == 1


def test_github_ssh_transport_uses_port_443_and_preserves_options(monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -i /tmp/release-key")

    configured = configure_github_ssh_transport(
        "git@github.com:example/download-data.git"
    )

    assert configured is True
    command = os.environ["GIT_SSH_COMMAND"]
    assert "-i /tmp/release-key" in command
    assert "Hostname=ssh.github.com" in command
    assert "-p 443" in command


def _snapshot_repo(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    checkout.mkdir()
    run_git(["init", "-b", "main"], checkout)
    run_git(["config", "user.name", "Fixture"], checkout)
    run_git(["config", "user.email", "fixture@example.invalid"], checkout)
    run_git(["remote", "add", "origin", str(remote)], checkout)
    (checkout / "manifest.json").write_text("old manifest")
    (checkout / "obsolete.csv").write_text("old partition")
    run_git(["add", "."], checkout)
    run_git(["commit", "-m", "Initial"], checkout)
    run_git(["push", "origin", "main"], checkout)
    initial = run_git(["rev-parse", "HEAD"], checkout)
    (checkout / "obsolete.csv").unlink()
    (checkout / "manifest.json").write_text("new manifest")
    for number in range(6):
        (checkout / f"partition {number}.csv").write_text(str(number) * 40)
    run_git(["add", "-A"], checkout)
    run_git(["commit", "-m", "Snapshot"], checkout)
    return remote, checkout, initial


def test_snapshot_upload_keeps_public_branch_atomic_and_cleans_staging(tmp_path, monkeypatch):
    import scripts.publish_download_repo as publisher

    remote, checkout, initial = _snapshot_repo(tmp_path)
    original = publisher.run_git_with_retry
    stages = []

    def observe(args, cwd, **kwargs):
        result = original(args, cwd, **kwargs)
        if ":refs/heads/globalid-upload/" in args[-1]:
            assert run_git(["rev-parse", "main"], remote) == initial
            assert run_git(["show", "main:manifest.json"], remote) == "old manifest"
            stages.append(args[-1].split(":")[0])
        return result

    monkeypatch.setattr(publisher, "run_git_with_retry", observe)
    push_download_snapshot(checkout, "main", batch_bytes=64)
    assert len(stages) >= 4
    assert run_git(["rev-parse", "main"], remote) == run_git(["rev-parse", "HEAD"], checkout)
    assert run_git(["ls-tree", "-r", "main"], remote) == run_git(["ls-tree", "-r", "HEAD"], checkout)
    assert run_git(["for-each-ref", "--format=%(refname)", "refs/heads/globalid-upload"], remote) == ""


def test_snapshot_upload_resumes_after_interrupted_batch(tmp_path, monkeypatch):
    import scripts.publish_download_repo as publisher

    remote, checkout, initial = _snapshot_repo(tmp_path)
    original = publisher.run_git_with_retry
    pushed = []

    def interrupt(args, cwd, **kwargs):
        if ":refs/heads/globalid-upload/" in args[-1]:
            if len(pushed) == 2:
                raise RuntimeError("simulated network interruption")
            pushed.append(args[-1])
        return original(args, cwd, **kwargs)

    monkeypatch.setattr(publisher, "run_git_with_retry", interrupt)
    with pytest.raises(RuntimeError, match="simulated network"):
        push_download_snapshot(checkout, "main", batch_bytes=64)
    assert run_git(["rev-parse", "main"], remote) == initial
    staged = run_git(["for-each-ref", "--format=%(objectname)", "refs/heads/globalid-upload"], remote)
    monkeypatch.setattr(publisher, "run_git_with_retry", original)
    # Recreated commits can have another ID; the content-addressed staging ref survives.
    run_git(["commit", "--amend", "-m", "Recreated snapshot"], checkout)
    push_download_snapshot(checkout, "main", batch_bytes=64)
    assert run_git(["rev-parse", "main"], remote) == run_git(["rev-parse", "HEAD"], checkout)
    assert staged


def test_snapshot_upload_small_changes_need_no_staging_branch(tmp_path, monkeypatch):
    import scripts.publish_download_repo as publisher

    remote, checkout, _ = _snapshot_repo(tmp_path)
    original = publisher.run_git_with_retry
    pushes = []

    def observe(args, cwd, **kwargs):
        if args[0] == "push":
            pushes.append(args)
        return original(args, cwd, **kwargs)

    monkeypatch.setattr(publisher, "run_git_with_retry", observe)
    push_download_snapshot(checkout, "main", batch_bytes=1024)
    assert pushes == [["push", "origin", "main"]]
    assert run_git(["rev-parse", "main"], remote) == run_git(["rev-parse", "HEAD"], checkout)


def test_snapshot_upload_rejects_oversized_blob_before_push(tmp_path):
    remote, checkout, initial = _snapshot_repo(tmp_path)
    with pytest.raises(RuntimeError, match="blob exceeds"):
        push_download_snapshot(checkout, "main", batch_bytes=10)
    assert run_git(["rev-parse", "main"], remote) == initial


def test_snapshot_staging_reuses_uploaded_objects_under_remote_pack_limit(tmp_path):
    import random

    remote, checkout, initial = _snapshot_repo(tmp_path)
    for number in range(6):
        (checkout / f"partition {number}.csv").write_bytes(random.Random(number).randbytes(2048))
    run_git(["add", "-A"], checkout)
    run_git(["commit", "--amend", "--no-edit"], checkout)
    run_git(["config", "receive.maxInputSize", "4096"], remote)
    # The unbatched snapshot exceeds this limit, so success also verifies the
    # final push negotiates and reuses objects received on the staging ref.
    with pytest.raises(RuntimeError, match="pack exceeds maximum allowed size"):
        push_branch(checkout, "main", attempts=1)
    assert run_git(["rev-parse", "main"], remote) == initial
    push_download_snapshot(checkout, "main", batch_bytes=2500)
    assert run_git(["rev-parse", "main"], remote) == run_git(["rev-parse", "HEAD"], checkout)


def _in_place_publication(tmp_path, monkeypatch):
    import scripts.publish_download_repo as publisher

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    source = tmp_path / "downloads"
    source.mkdir()
    _write_source(source)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Fixture")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "fixture@example.invalid")
    monkeypatch.setattr(publisher, "verify_public_files", lambda *_args: None)
    args = SimpleNamespace(
        repo_url=str(remote), branch="main", source_dir=source, workdir=source,
        push=True, verify_checksums=False, verify_remote=False, commit_message="Downloads",
    )
    return publisher, args


@pytest.mark.parametrize("corrupt", [False, True])
def test_frozen_candidate_reuses_export_worktree_without_reset_and_checks_changes(tmp_path, monkeypatch, corrupt):
    import shutil
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    publisher.publish(args)
    candidate = tmp_path / "candidate"
    shutil.copytree(args.workdir, candidate, ignore=shutil.ignore_patterns(".git"))
    _write_source(candidate, b"new data")
    sync_managed_assets(candidate, args.workdir)
    if corrupt:
        path = args.workdir / "diseases/d007/2026-2029.csv"
        path.write_bytes(b"x" * path.stat().st_size)
    args.source_dir = candidate
    def forbid_reset(*_args):
        raise AssertionError("Frozen candidate must preserve the export worktree")
    monkeypatch.setattr(publisher, "ensure_repo", forbid_reset)
    if corrupt:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            publisher.publish(args)
    else:
        publisher.publish(args)
        assert run_git(["rev-parse", "main"], tmp_path / "remote.git") == run_git(["rev-parse", "HEAD"], args.workdir)
        assert (args.workdir / "diseases/d007/2026-2029.csv").read_bytes() == b"new datacsv"


def test_verified_in_place_noop_has_no_network_or_validation(tmp_path, monkeypatch):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    def no_public_check(*_args):
        raise AssertionError("Default publication must not probe public URLs")
    monkeypatch.setattr(publisher, "verify_public_files", no_public_check)
    publisher.publish(args)
    receipt = json.loads(publisher._receipt_path(args.workdir).read_text())
    assert receipt["verified_at"]
    journal = publisher._receipt_path(args.workdir).parent / "download-publications.jsonl"
    assert [json.loads(line) for line in journal.read_text().splitlines()] == [receipt]
    assert (tmp_path / ".downloads.git").is_dir()
    assert (args.workdir / ".git").is_file()
    assert run_git(["config", "core.trustctime"], args.workdir) == "false"
    # Production rollback backups create links to immutable partitions. Their
    # ctime changes must not invalidate the unchanged-publication checkpoint.
    asset = args.workdir / "diseases/d007/2026-2029.csv"
    os.link(asset, tmp_path / "backup.csv")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("verified unchanged exports must not touch network or validate files")

    monkeypatch.setattr(publisher, "run_git_with_retry", forbidden)
    monkeypatch.setattr(publisher, "validate_source", forbidden)
    monkeypatch.setattr(publisher, "verify_public_files", forbidden)
    publisher.publish(args)
    assert len(journal.read_text().splitlines()) == 1


@pytest.mark.parametrize("failure_stage", ["push", "verify"])
def test_in_place_retries_clean_unverified_commit(tmp_path, monkeypatch, failure_stage):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    args.verify_remote = failure_stage == "verify"
    original_push = publisher.push_download_snapshot

    def failed(*_args, **_kwargs):
        raise RuntimeError("interrupted publication")

    monkeypatch.setattr(publisher, "push_download_snapshot" if failure_stage == "push" else "verify_public_files", failed)
    with pytest.raises(RuntimeError, match="interrupted publication"):
        publisher.publish(args)
    assert not publisher._receipt_path(args.workdir).exists()
    assert not run_git(["status", "--porcelain"], args.workdir)
    pending = run_git(["rev-parse", "HEAD"], args.workdir)
    monkeypatch.setattr(publisher, "push_download_snapshot", original_push)
    monkeypatch.setattr(publisher, "verify_public_files", lambda *_args: None)
    publisher.publish(args)
    assert publisher.publication_is_verified(args.workdir, args.repo_url, args.branch)
    assert run_git(["rev-parse", "main"], tmp_path / "remote.git") == pending


def test_in_place_updates_and_explicit_audit(tmp_path, monkeypatch):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    publisher.publish(args)
    first = run_git(["rev-parse", "HEAD"], args.workdir)
    _write_source(args.workdir, payload=b"historical revision")
    publisher.publish(args)
    assert run_git(["rev-parse", "HEAD"], args.workdir) != first
    checks = []
    monkeypatch.setattr(publisher, "verify_public_files", lambda *_args: checks.append(True))
    args.verify_remote = True
    publisher.publish(args)
    assert checks == [True]


def test_changed_manifest_cannot_trust_old_checksum_for_unchanged_blob(tmp_path, monkeypatch):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    publisher.publish(args)
    path = args.workdir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["diseases"][0]["parts"][0]["files"]["csv"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="Manifest checksum mismatch"):
        publisher.publish(args)


def test_export_repository_recovers_pointer_without_resetting_pending_commit(tmp_path, monkeypatch):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    publisher.publish(args)
    head = run_git(["rev-parse", "HEAD"], args.workdir)
    (args.workdir / ".git").unlink()  # First-run artifact rollback removes this pointer.
    publisher.prepare_export_repo(args.repo_url, args.branch, args.workdir)
    assert run_git(["rev-parse", "HEAD"], args.workdir) == head


async def test_preflight_reuses_verified_export_without_remote_probe(tmp_path, monkeypatch):
    from src.services.data_release.checks import download_repo_check
    from unittest.mock import AsyncMock

    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    source = tmp_path / "exports" / "site-downloads"
    source.parent.mkdir()
    args.source_dir.rename(source)
    args.source_dir = args.workdir = source
    publisher.publish(args)
    capture = AsyncMock(side_effect=AssertionError("unexpected remote probe"))
    result = await download_repo_check(
        repo_url=args.repo_url, branch=args.branch,
        raw_base_url="https://raw.githubusercontent.com/example/data/main",
        root_dir=tmp_path, github_ssh_prefixes=(), run_capture=capture,
    )
    assert result["blockers"] == []
    assert result["payload"]["cached_publication"] is True
    capture.assert_not_awaited()


@pytest.mark.parametrize("batch_bytes", [64, 1024 * 1024])
def test_in_place_rejects_remote_advancement_without_overwriting_it(tmp_path, monkeypatch, batch_bytes):
    publisher, args = _in_place_publication(tmp_path, monkeypatch)
    publisher.publish(args)
    original_push = publisher.push_download_snapshot
    monkeypatch.setattr(publisher, "push_download_snapshot", lambda workdir, branch: original_push(workdir, branch, batch_bytes=batch_bytes))
    peer = tmp_path / "peer"
    subprocess.run(["git", "clone", "-b", "main", args.repo_url, str(peer)], check=True, capture_output=True)
    run_git(["config", "user.name", "Peer"], peer)
    run_git(["config", "user.email", "peer@example.invalid"], peer)
    (peer / "README.md").write_text("Concurrent publication")
    run_git(["add", "."], peer)
    run_git(["commit", "-m", "Concurrent publication"], peer)
    run_git(["push", "origin", "main"], peer)
    advanced = run_git(["rev-parse", "HEAD"], peer)
    _write_source(args.workdir, payload=b"local revision")
    with pytest.raises(RuntimeError, match="non-fast-forward"):
        publisher.publish(args)
    assert run_git(["rev-parse", "main"], tmp_path / "remote.git") == advanced
    assert not publisher.publication_is_verified(args.workdir, args.repo_url, args.branch)
