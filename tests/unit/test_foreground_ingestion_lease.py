"""Foreground imports renew leases and fence cleanup after ownership changes."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.task import TaskStatus
from src.services.ingestion import foreground


def database(monkeypatch):
    task = SimpleNamespace(task_uuid="foreground-task", status=TaskStatus.RUNNING,
                           metadata_={"retained": "evidence"})
    db = SimpleNamespace(execute=AsyncMock(side_effect=lambda *_: SimpleNamespace(
        scalar_one_or_none=lambda: task)))

    @asynccontextmanager
    async def get_db():
        yield db

    monkeypatch.setattr(foreground, "get_db", get_db)
    return task, db


async def test_long_foreground_body_renews_multiple_times_and_exit_stops_heartbeat(monkeypatch):
    task, _ = database(monkeypatch)
    renewed = asyncio.Event()
    owners = []

    async def heartbeat(task_uuid, owner):
        assert task_uuid == task.task_uuid
        assert task.metadata_["task_lease"]["owner"] == owner
        owners.append(owner)
        if len(owners) >= 3:
            renewed.set()
        return True

    monkeypatch.setattr(foreground.task_manager, "heartbeat_task_lease", heartbeat)
    async with foreground.foreground_task_lease(task.task_uuid, heartbeat_seconds=.001) as lease:
        await asyncio.wait_for(renewed.wait(), timeout=1)
        assert lease.owner.startswith("foreground:")
        assert "released_at" not in task.metadata_["task_lease"]
    calls_at_exit = len(owners)
    await asyncio.sleep(.01)
    assert len(owners) == calls_at_exit
    assert set(owners) == {lease.owner}
    assert task.metadata_["task_lease"]["released_at"]
    assert task.metadata_["retained"] == "evidence"
    assert task.status == TaskStatus.RUNNING  # terminal status belongs to caller


async def test_body_exception_releases_lease_and_preserves_original_error(monkeypatch):
    task, _ = database(monkeypatch)
    heartbeat = AsyncMock(return_value=True)
    monkeypatch.setattr(foreground.task_manager, "heartbeat_task_lease", heartbeat)
    with pytest.raises(ValueError, match="source failed"):
        async with foreground.foreground_task_lease(task.task_uuid):
            raise ValueError("source failed")
    assert task.metadata_["task_lease"]["released_at"]
    heartbeat.assert_not_awaited()


async def test_ownership_loss_stops_body_without_releasing_or_failing_replacement(monkeypatch):
    task, db = database(monkeypatch)
    replacement = {"owner": "replacement-worker", "heartbeat_at": "replacement-evidence"}

    async def heartbeat(*_):
        task.metadata_ = {"retained": "new-owner-evidence", "task_lease": deepcopy(replacement)}
        return False

    monkeypatch.setattr(foreground.task_manager, "heartbeat_task_lease", heartbeat)
    with pytest.raises(foreground.ForegroundTaskLeaseLostError):
        async with foreground.foreground_task_lease(task.task_uuid, heartbeat_seconds=.001) as lease:
            await asyncio.Event().wait()
    assert task.status == TaskStatus.RUNNING
    assert task.metadata_ == {"retained": "new-owner-evidence", "task_lease": replacement}
    with pytest.raises(foreground.ForegroundTaskLeaseLostError):
        await lease.owned_task(db)
    assert task.metadata_["task_lease"] == replacement


async def test_cannot_claim_an_unreleased_owner(monkeypatch):
    task, _ = database(monkeypatch)
    task.metadata_["task_lease"] = {"owner": "worker-current", "heartbeat_at": "unchanged"}
    original = deepcopy(task.metadata_)
    with pytest.raises(foreground.ForegroundTaskLeaseLostError, match="unreleased"):
        async with foreground.foreground_task_lease(task.task_uuid):
            pytest.fail("entered a task owned by another process")
    assert task.metadata_ == original


async def test_external_cancellation_releases_lease_and_stops_background_task(monkeypatch):
    task, _ = database(monkeypatch)
    heartbeat = AsyncMock(return_value=True)
    monkeypatch.setattr(foreground.task_manager, "heartbeat_task_lease", heartbeat)
    entered = asyncio.Event()

    async def run():
        async with foreground.foreground_task_lease(task.task_uuid, heartbeat_seconds=.001):
            entered.set()
            await asyncio.Event().wait()

    operation = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    calls_at_exit = heartbeat.await_count
    await asyncio.sleep(.01)
    assert heartbeat.await_count == calls_at_exit
    assert task.metadata_["task_lease"]["released_at"]
