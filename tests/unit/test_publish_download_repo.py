from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from scripts.publish_download_repo import (
    configure_github_ssh_transport,
    ensure_commit_identity,
    ensure_repo,
    push_branch,
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
