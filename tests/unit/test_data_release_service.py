from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(minutes=10),
        completed_at=now - timedelta(minutes=5),
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=720))

    async def recent_failed_release_tasks():
        return [failed_task]

    monkeypatch.setattr(service, "_recent_failed_release_tasks", recent_failed_release_tasks)

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
        metadata_={"release_job_id": "site-release"},
        created_at=now - timedelta(hours=24),
        completed_at=now - timedelta(hours=24),
    )
    other_job_task = SimpleNamespace(
        task_uuid="other-job-failure",
        metadata_={"release_job_id": "other-release"},
        created_at=now,
        completed_at=now,
    )

    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(auto_failure_cooldown_minutes=60))

    async def recent_failed_release_tasks():
        return [old_task, other_job_task]

    monkeypatch.setattr(service, "_recent_failed_release_tasks", recent_failed_release_tasks)

    assert await service._release_failure_cooldown_reason("site-release") is None
