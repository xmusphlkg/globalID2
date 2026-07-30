from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.domain import TaskStatus
from src.services import data_release_service as release_module
from src.services.data_release_service import DataReleaseService


@pytest.mark.asyncio
async def test_subscription_options_sync_auto_failure_is_non_blocking(monkeypatch):
    service = DataReleaseService()
    entries = []

    monkeypatch.setattr(service, "_subscription_options_sync_plan", lambda: (True, "auto sync enabled"))
    monkeypatch.setattr(service, "_subscription_options_sync_is_strict", lambda: False)

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("fetch failed")

    async def add_workbook_entry(_task_uuid, **kwargs):
        entries.append(kwargs)

    monkeypatch.setattr(service, "_run_logged_command", fail_command)
    monkeypatch.setattr(
        "src.services.data_release_service.task_manager.add_workbook_entry",
        add_workbook_entry,
    )

    synced = await service._sync_subscription_options_if_needed("task-1", job_id="site-release")

    assert synced is False
    assert entries
    assert entries[0]["entry_type"] == "warning"
    assert entries[0]["title"] == "Subscription Options Sync Failed"


@pytest.mark.asyncio
async def test_subscription_options_sync_strict_failure_is_blocking(monkeypatch):
    service = DataReleaseService()

    monkeypatch.setattr(service, "_subscription_options_sync_plan", lambda: (True, "strict sync enabled"))
    monkeypatch.setattr(service, "_subscription_options_sync_is_strict", lambda: True)

    async def fail_command(*_args, **_kwargs):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(service, "_run_logged_command", fail_command)

    with pytest.raises(RuntimeError, match="fetch failed"):
        await service._sync_subscription_options_if_needed("task-1", job_id="site-release")


@pytest.mark.asyncio
async def test_release_failure_cooldown_suppresses_recent_failed_auto_release(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    failed_task = SimpleNamespace(
        task_uuid="failed-release-1",
        status=TaskStatus.FAILED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(minutes=10),
        completed_at=now - timedelta(minutes=5),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=720))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return failed_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    reason = await service._release_failure_cooldown_reason("site-release")

    assert reason is not None
    assert "failed-release-1" in reason
    assert "cooling down" in reason


@pytest.mark.asyncio
async def test_release_failure_cooldown_ignores_old_or_other_jobs(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    old_task = SimpleNamespace(
        task_uuid="old-failure",
        status=TaskStatus.FAILED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(hours=24),
        completed_at=now - timedelta(hours=24),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=60))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return old_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    assert await service._release_failure_cooldown_reason("site-release") is None


@pytest.mark.asyncio
async def test_release_failure_cooldown_clears_after_later_success(monkeypatch):
    service = DataReleaseService()
    now = datetime.now(timezone.utc)
    successful_task = SimpleNamespace(
        task_uuid="successful-release",
        status=TaskStatus.COMPLETED,
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(minutes=1),
        completed_at=now - timedelta(minutes=1),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=720))

    async def latest_terminal_release_task(job_id):
        assert job_id == "site-release"
        return successful_task

    monkeypatch.setattr(service, "_latest_terminal_release_task", latest_terminal_release_task)

    assert await service._release_failure_cooldown_reason("site-release") is None


def test_cloudflare_deploy_command_targets_explicit_production_branch():
    service = DataReleaseService()

    command = service._cloudflare_deploy_command(
        project_name="globalidv2",
        branch="master",
        source_commit="a" * 40,
        commit_message="publish verified release",
        commit_dirty=False,
    )

    assert command[command.index("--branch") + 1] == "master"
    assert command[command.index("--commit-hash") + 1] == "a" * 40
    assert command[command.index("--commit-message") + 1] == "publish verified release"
    assert "--commit-dirty=false" in command


def test_cloudflare_deployment_match_requires_production_branch_and_commit():
    service = DataReleaseService()
    identity = {
        "deployment_branch": "master",
        "source_commit": "a" * 40,
    }
    deployment = {
        "environment": "production",
        "status": "success",
        "branch": "master",
        "commit_hash": "a" * 40,
    }

    assert service._cloudflare_deployment_matches(deployment, identity) is True
    assert service._cloudflare_deployment_matches(
        {**deployment, "environment": "preview"},
        identity,
    ) is False
    assert service._cloudflare_deployment_matches(
        {**deployment, "branch": "feature"},
        identity,
    ) is False


def test_site_release_manifest_records_visual_modules(monkeypatch, tmp_path):
    astro_dir = tmp_path / "astro-site"
    assets_dir = astro_dir / "dist" / "_astro"
    assets_dir.mkdir(parents=True)
    (assets_dir / "EpidemicCurve.ABC123.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "DiseaseMonthlyBar.DEF456.js").write_text("export {}", encoding="utf-8")
    (assets_dir / "unrelated.GHI789.js").write_text("export {}", encoding="utf-8")
    manifest_path = astro_dir / "dist" / "release.json"
    monkeypatch.setattr(release_module, "ASTRO_DIR", astro_dir)
    monkeypatch.setattr(release_module, "SITE_RELEASE_MANIFEST", manifest_path)

    identity = {
        "release_id": "20260730T120000Z-abcdef123456",
        "built_at": "2026-07-30T12:00:00+00:00",
        "source_branch": "feature",
        "source_commit": "a" * 40,
        "deployment_branch": "master",
        "commit_dirty": False,
    }
    payload = DataReleaseService()._write_site_release_manifest(identity)

    assert payload["visual_modules"] == [
        "DiseaseMonthlyBar.DEF456.js",
        "EpidemicCurve.ABC123.js",
    ]
    assert manifest_path.read_text(encoding="utf-8").endswith("\n")
