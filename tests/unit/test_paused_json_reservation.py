"""A paused retry keeps its candidate without monopolizing the other JSON feed."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.core.release_admission import ReleaseAdmission
from src.domain import Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty
from src.services import data_release_service as release_module, task_worker
from src.services.data_release_service import DataReleaseJobConfig
from tests.unit.test_release_dirty_selection import release_db  # noqa: F401
from tests.unit.test_release_store_exclusion import worker_database  # noqa: F401


def _input(line, *, legacy=False):
    value = {"data_only": True, "release_job_id": "site-release",
             "manual_trigger": False, "trigger": "upstream_completion",
             "publish_dirty_ids": [123], "continuation_dirty_ids": [456]}
    if not legacy:
        value["data_line"] = line
    return value


def _owner(line, status=TaskStatus.RETRYING, *, legacy=False):
    return Task(task_uuid="parked", task_name="Existing JSON", task_type=TaskType.EXPORT_DATA,
        status=status, created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        input_data=_input(line, legacy=legacy),
        metadata_={"release_job_id": "site-release", "automatic_retry": {
            "status": "scheduled", "scheduled_attempts": 1, "max_attempts": 3,
            "next_attempt_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}})


def _pause(monkeypatch, paused):
    for module in (release_module, task_worker):
        monkeypatch.setattr(module, "is_automatic_json_paused", lambda line: line in paused)


@pytest.mark.parametrize("parked_line,legacy", [
    ("surveillance", False), ("surveillance", True), ("situation", False),
])
@pytest.mark.parametrize("same_line", [False, True])
async def test_enqueue_bypasses_only_other_paused_retry(release_db, monkeypatch, parked_line, legacy, same_line):
    maker, service, _ = release_db
    _pause(monkeypatch, {parked_line})
    incoming = parked_line if same_line else "situation" if parked_line == "surveillance" else "surveillance"
    original = _owner(parked_line, legacy=legacy)
    async with maker() as db:
        db.add_all([original, PublishDirty(line=incoming, scope_key="US" if incoming == "surveillance" else "*",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))])
        await db.commit()
        original_input, original_metadata = dict(original.input_data), dict(original.metadata_)
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="dashboard-realtime", trigger_task_uuid=None, data_only=True, data_line=incoming)
    assert result["status"] == ("skipped" if same_line else "queued")
    if same_line:
        assert result["task_uuid"] == "parked"
    async with maker() as db:
        parked = (await db.execute(select(Task).where(Task.task_uuid == "parked"))).scalar_one()
        assert parked.status == TaskStatus.RETRYING
        assert parked.input_data == original_input
        assert parked.metadata_ == original_metadata


@pytest.mark.parametrize("owner_status", [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING])
@pytest.mark.parametrize("paused", [False, True])
async def test_enqueue_preserves_live_reservations_and_resume(release_db, monkeypatch, owner_status, paused):
    maker, service, _ = release_db
    _pause(monkeypatch, {"situation"} if paused else set())
    async with maker() as db:
        db.add_all([_owner("situation", owner_status), PublishDirty(line="surveillance", scope_key="US",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))])
        await db.commit()
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True)
    assert result["status"] == ("queued" if paused and owner_status == TaskStatus.RETRYING else "skipped")


@pytest.mark.parametrize("parked_line,legacy", [
    ("surveillance", False), ("surveillance", True), ("situation", False),
])
@pytest.mark.parametrize("same_line", [False, True])
async def test_worker_claim_bypasses_only_other_paused_retry(worker_database, monkeypatch, parked_line, legacy, same_line):
    _pause(monkeypatch, {parked_line})
    incoming = parked_line if same_line else "situation" if parked_line == "surveillance" else "surveillance"
    async with worker_database() as db:
        db.add_all([_owner(parked_line, legacy=legacy), Task(task_uuid="next", task_name="Next JSON",
            task_type=TaskType.EXPORT_DATA, status=TaskStatus.QUEUED, input_data=_input(incoming))])
        await db.commit()
    result = await task_worker._claim_next_task_uuid("worker")
    assert result == (None if same_line else ("next", TaskType.EXPORT_DATA))
    async with worker_database() as db:
        parked = (await db.execute(select(Task).where(Task.task_uuid == "parked"))).scalar_one()
        assert parked.status == TaskStatus.RETRYING
        assert parked.input_data["continuation_dirty_ids"] == [456]
        assert parked.metadata_["automatic_retry"]["status"] == "scheduled"


@pytest.mark.parametrize("owner_status", [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING])
async def test_worker_keeps_live_or_resumed_json_slot_serial(worker_database, monkeypatch, owner_status):
    # A resumed retry reserves both lines again. A paused queued/running task
    # continues through its ordinary lifecycle and still owns the shared slot.
    _pause(monkeypatch, set() if owner_status == TaskStatus.RETRYING else {"situation"})
    async with worker_database() as db:
        db.add_all([_owner("situation", owner_status), Task(task_uuid="next", task_name="Next JSON",
            task_type=TaskType.EXPORT_DATA, status=TaskStatus.QUEUED, input_data=_input("surveillance"))])
        await db.commit()
    if owner_status == TaskStatus.QUEUED:
        assert await task_worker._claim_next_task_uuid("worker") == ("parked", TaskType.EXPORT_DATA)
    assert await task_worker._claim_next_task_uuid("worker") is None


async def test_resume_waits_for_other_feed_then_requeues_same_candidate(release_db, monkeypatch):
    maker, service, _ = release_db
    paused = {"situation"}
    _pause(monkeypatch, paused)
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC",
        surveillance_json_enabled=True, situation_json_enabled=True))
    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(task_worker, "release_admission", ReleaseAdmission())
    monkeypatch.setattr(task_worker, "_claimed_data_release_uuids", set())

    @asynccontextmanager
    async def database():
        async with maker() as db:
            yield db

    monkeypatch.setattr(task_worker, "get_database", database)
    owner = _owner("situation")
    async with maker() as db:
        db.add_all([owner, Task(task_uuid="other", task_name="Other JSON", task_type=TaskType.EXPORT_DATA,
            status=TaskStatus.QUEUED, input_data=_input("surveillance"))])
        await db.commit()
        original_input = dict(owner.input_data)
        original_retry = dict(owner.metadata_["automatic_retry"])
    assert await service.requeue_due_automatic_retries() == []
    assert await task_worker._claim_next_task_uuid("worker") == ("other", TaskType.EXPORT_DATA)
    paused.clear()
    assert await service.requeue_due_automatic_retries() == []
    assert await task_worker._claim_next_task_uuid("worker") is None
    async with maker() as db:
        retry = (await db.execute(select(Task).where(Task.task_uuid == "parked"))).scalar_one()
        assert retry.status == TaskStatus.RETRYING
        assert retry.input_data == original_input
        assert retry.metadata_["automatic_retry"] == original_retry
        other = (await db.execute(select(Task).where(Task.task_uuid == "other"))).scalar_one()
        other.status = TaskStatus.COMPLETED
        await db.commit()
    assert await service.requeue_due_automatic_retries() == ["parked"]
    assert await task_worker._claim_next_task_uuid("worker") == ("parked", TaskType.EXPORT_DATA)
    async with maker() as db:
        retry = (await db.execute(select(Task).where(Task.task_uuid == "parked"))).scalar_one()
        assert retry.input_data["publish_dirty_ids"] == original_input["publish_dirty_ids"]
        assert retry.input_data["continuation_dirty_ids"] == original_input["continuation_dirty_ids"]
        assert retry.metadata_["automatic_retry"]["scheduled_attempts"] == 1
