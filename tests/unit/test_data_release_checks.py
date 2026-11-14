from __future__ import annotations

import pytest

from src.services.data_release import checks


def website_history():
    return {"schema_version": 1, "releases": [{
        "version": "0.1.0", "date": "2026-01-01",
        **{key: "Published website release" for key in ("titleEn", "titleZh", "titleFr", "summaryEn", "summaryZh", "summaryFr")},
        "sections": [{"kind": "improved", "labelEn": "Changes", "labelZh": "更新", "labelFr": "Modifications",
                      "items": [{"en": "Updated", "zh": "更新", "fr": "Mis à jour"}]}],
    }]}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201])
async def test_public_json_requires_http_200(monkeypatch, status):
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value = response
    response.getcode.return_value = status
    response.read.return_value = b'{"schema_version":1}'
    monkeypatch.setattr(checks.urlrequest, "urlopen", lambda *args, **kwargs: response)
    if status == 200:
        assert await checks.public_json("https://public.example/feed.json") == {"schema_version": 1}
    else:
        with pytest.raises(RuntimeError, match="HTTP 201"):
            await checks.public_json("https://public.example/feed.json")


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
        if (
            command[1] == "ls-remote"
            and "Hostname=ssh.github.com" not in env["GIT_SSH_COMMAND"]
        ):
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
        branch="main",
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
        "build_digest": "a" * 64,
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
        if url == "https://public.example/site-data/updates/changelog.json":
            return website_history()
        assert url.startswith("https://globalid.pages.dev/release.json?")
        return {
            "release_id": "release-1",
            "deployment_branch": "main",
            "source_commit": "abc123",
            "build_digest": "a" * 64,
        }

    result = await checks.verify_cloudflare_production_release(
        project_name="globalid",
        subdomain="globalid.pages.dev",
        release_identity=identity,
        latest_deployment=latest_deployment,
        fetch_public_json=public_json,
        public_origin="https://public.example/",
    )

    assert result["verified"] is True
    assert result["production_url"] == "https://globalid.pages.dev"
    assert result["public_dependencies"]["changelog"] == {
        "url": "https://public.example/site-data/updates/changelog.json",
        "schema_version": 1, "release_count": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["404", "schema", "empty", "translation", "date"])
async def test_matching_pages_release_cannot_hide_unavailable_public_changelog(monkeypatch, fault):
    from unittest.mock import AsyncMock
    from urllib.error import HTTPError

    identity = {"release_id": "new-release", "deployment_branch": "main", "source_commit": "abc123", "build_digest": "a" * 64}
    deployment = {"environment": "production", "status": "success", "branch": "main", "commit_hash": "abc123"}
    urls = []

    async def public_json(url):
        urls.append(url)
        if "/release.json?" in url:
            return identity
        assert url == "https://public.example/site-data/updates/changelog.json"
        if fault == "404":
            raise HTTPError(url, 404, "Not Found", {}, None)
        history = website_history()
        if fault == "schema": history["schema_version"] = 2
        if fault == "empty": history["releases"] = []
        if fault == "translation": history["releases"][0]["sections"][0]["items"][0].pop("fr")
        if fault == "date": history["releases"][0]["date"] = "2026-02-30"
        return history

    monkeypatch.setattr(checks.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match="deploy the current cloudflare/surveillance-data Worker"):
        await checks.verify_cloudflare_production_release(project_name="globalid", subdomain="globalid.pages.dev",
            release_identity=identity, latest_deployment=AsyncMock(return_value=deployment),
            fetch_public_json=public_json, public_origin="https://public.example")
    assert sum("/release.json?" in url for url in urls) == 8
    assert urls.count("https://public.example/site-data/updates/changelog.json") == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("production_digest", [None, "old-build"])
async def test_verification_rejects_previous_attempt_at_same_revision(
    monkeypatch, production_digest
):
    identity = {
        "release_id": "release-1",
        "deployment_branch": "main",
        "source_commit": "abc123",
        "build_digest": "new-build",
    }

    async def latest(_project, **_kwargs):
        return {
            "environment": "production",
            "status": "success",
            "branch": "main",
            "commit_hash": "abc123",
        }

    async def manifest(url):
        assert "build=new-build" in url
        return {**identity, "build_digest": production_digest}

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(checks.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="could not be verified"):
        await checks.verify_cloudflare_production_release(
            project_name="globalid",
            subdomain="globalid.pages.dev",
            release_identity=identity,
            latest_deployment=latest,
            fetch_public_json=manifest,
        )
