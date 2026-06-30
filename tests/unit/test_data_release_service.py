from __future__ import annotations

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
