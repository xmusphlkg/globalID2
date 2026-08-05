from __future__ import annotations

import pytest

from src.services.data_release import checks


def test_normalize_cloudflare_deployment_projects_stable_fields():
    deployment = {
        "id": "deploy-1",
        "url": "https://deploy.example",
        "environment": "production",
        "created_on": "2026-08-05T01:02:03Z",
        "latest_stage": {"status": "success", "ignored": True},
        "deployment_trigger": {
            "metadata": {
                "branch": "main",
                "commit_hash": "abc123",
                "commit_message": "publish",
                "commit_dirty": 0,
                "ignored": "value",
            }
        },
        "ignored": "value",
    }

    assert checks.normalize_cloudflare_deployment(deployment) == {
        "id": "deploy-1",
        "url": "https://deploy.example",
        "environment": "production",
        "created_on": "2026-08-05T01:02:03Z",
        "status": "success",
        "branch": "main",
        "commit_hash": "abc123",
        "commit_message": "publish",
        "commit_dirty": False,
    }


@pytest.mark.asyncio
async def test_raw_archive_check_uses_ssh_over_443_after_primary_failure(tmp_path):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    calls: list[tuple[list[str], dict[str, str]]] = []

    async def run_capture(command, *, env, **_kwargs):
        calls.append((command, env))
        if command[1] == "ls-remote" and "Hostname=ssh.github.com" not in env["GIT_SSH_COMMAND"]:
            return {"returncode": 1, "stdout": "port 22 blocked"}
        return {"returncode": 0, "stdout": "ok"}

    result = await checks.raw_archive_check(
        enabled=True,
        repo_url="git@github.com:example/archive.git",
        source_dir=source_dir,
        repository_dir=tmp_path / "archive",
        branch="main",
        root_dir=tmp_path,
        github_ssh_prefixes=("git@github.com:", "ssh://git@github.com/"),
        run_capture=run_capture,
    )

    assert result["blockers"] == []
    assert result["payload"]["read_access_ok"] is True
    assert result["payload"]["write_access_ok"] is True
    assert result["payload"]["ssh_transport"] == "github-ssh-over-443"
    assert calls[-1][0][-1] == "HEAD:refs/heads/__globalid_write_probe__/main"
    assert "Hostname=ssh.github.com" in calls[-1][1]["GIT_SSH_COMMAND"]


@pytest.mark.asyncio
async def test_download_repo_check_missing_url_does_not_run_commands(tmp_path):
    async def unexpected_capture(*_args, **_kwargs):
        raise AssertionError("command execution must not occur")

    result = await checks.download_repo_check(
        repo_url="",
        branch="snapshot-v2",
        raw_base_url="",
        root_dir=tmp_path,
        github_ssh_prefixes=("git@github.com:",),
        run_capture=unexpected_capture,
    )

    assert result["blockers"] == ["Missing GITHUB_DATA_SHARE_REPO_URL."]
    assert result["payload"]["read_access_ok"] is False
    assert result["payload"]["write_access_ok"] is False


@pytest.mark.asyncio
async def test_cloudflare_check_normalizes_api_failure_without_deployment_lookup():
    latest_calls = 0

    async def api_json(_url: str):
        return {"success": False, "errors": [{"message": "denied"}]}

    async def latest_deployment(*_args, **_kwargs):
        nonlocal latest_calls
        latest_calls += 1
        return None

    result = await checks.cloudflare_check(
        project="globalid",
        token="token",
        account_id="account",
        api_json=api_json,
        latest_deployment=latest_deployment,
    )

    assert result["blockers"] == ["Cloudflare Pages project check failed."]
    assert result["payload"]["project_access_ok"] is False
    assert "denied" in result["payload"]["error"]
    assert latest_calls == 0


@pytest.mark.asyncio
async def test_verify_cloudflare_release_accepts_matching_normalized_payload():
    identity = {
        "release_id": "release-1",
        "deployment_branch": "main",
        "source_commit": "abc123",
    }

    async def latest_deployment(_project: str, *, environment: str):
        assert environment == "production"
        return {
            "environment": "production",
            "status": "success",
            "branch": "main",
            "commit_hash": "abc123",
        }

    async def public_json(url: str):
        assert url.startswith("https://globalid.pages.dev/release.json?")
        return {
            "release_id": "release-1",
            "deployment_branch": "main",
            "source_commit": "abc123",
        }

    result = await checks.verify_cloudflare_production_release(
        project_name="globalid",
        subdomain="globalid.pages.dev",
        release_identity=identity,
        latest_deployment=latest_deployment,
        fetch_public_json=public_json,
    )

    assert result["verified"] is True
    assert result["production_url"] == "https://globalid.pages.dev"
