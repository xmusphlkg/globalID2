"""Capacity recovery is bounded, restart-safe, and replays immutable originals."""
import hashlib
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import (
    AutomationJob,
    RunStage,
    ScheduledJobState,
    SourceArtifact,
    Task,
    TaskStatus,
    TaskType,
)
from src.services import cn_province_image_recovery as module
from src.services.ingestion.countries import cn_province as connector

NOW = datetime(2026, 11, 23, 8, tzinfo=timezone.utc)
JOB_ID = "cn-sn-monthly-weekly"


def metadata(*, panel=3):
    return {"province_ingestion": {"status": "needs_review", "revision_candidates": [],
        "source_audit": {"status": "needs_review", "image_reviews": [{"status": "needs_review", "audit": {
            "protocol": module.PROTOCOL, "panel_size": panel, "quorum": panel // 2 + 1,
            "available_native_models_at_start": 0, "documents": [{"sha256": "a" * 64}],
            "readings": [{"slot": n, "status": "absent", "reason": "insufficient_distinct_native_models",
                          "model": None} for n in range(1, panel + 1)],
        }}]}}}


def source_task(**overrides):
    return Task(task_uuid=str(uuid4()), task_type=TaskType.CRAWL_DATA,
                task_name="Original province report", status=TaskStatus.COMPLETED,
                country_id=1, created_at=NOW - timedelta(hours=1), completed_at=NOW - timedelta(minutes=30),
                input_data={"country_code": "CN-SN", "source": module.SOURCE,
                            "automation_job_id": JOB_ID, "process": True},
                metadata_=metadata(), **overrides)


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (AutomationJob, ScheduledJobState, Task, RunStage, SourceArtifact):
            await connection.run_sync(table.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions() as db:
            yield db
            await db.commit()

    monkeypatch.setattr(module, "get_database", database)
    task = source_task()
    job = AutomationJob(job_id=JOB_ID, name="Shaanxi", country_code="CN-SN", source=module.SOURCE,
                        enabled=True, interval_minutes=10080, revision_window_months=3)
    next_run = (NOW + timedelta(days=7)).replace(tzinfo=None)
    async with sessions() as db:
        db.add_all([job, task, ScheduledJobState(job_kind="ingestion", job_id=JOB_ID,
            next_run_at=next_run, last_task_uuid=task.task_uuid, last_status="completed"),
            RunStage(task_uuid=task.task_uuid, stage="collect", attempt=1, status="completed",
                     started_at=NOW - timedelta(minutes=32), finished_at=NOW - timedelta(minutes=31)),
            RunStage(task_uuid=task.task_uuid, stage="organize", attempt=1, status="completed",
                     started_at=NOW - timedelta(minutes=31), finished_at=NOW - timedelta(minutes=30))])
        await db.commit()
    capacity = AsyncMock(return_value={"quorum_available": True, "full_panel_available": False,
                                      "distinct_native_models": 2})
    monkeypatch.setattr(module, "native_model_capacity", capacity)

    async def enqueue(**kwargs):
        child = Task(task_uuid=str(uuid4()), task_type=TaskType.CRAWL_DATA,
            task_name="Recover images", status=TaskStatus.QUEUED, country_id=1, created_at=NOW,
            input_data={"country_code": kwargs["country_code"], "source": kwargs["source"],
                        "process": kwargs["process"], **kwargs["metadata"]}, metadata_={})
        async with sessions() as db:
            db.add(child)
            await db.commit()
        return SimpleNamespace(created=True, task=child)

    enqueue_mock = AsyncMock(side_effect=enqueue)
    monkeypatch.setattr(module.crawl_task_service, "enqueue_crawl_task", enqueue_mock)
    yield SimpleNamespace(sessions=sessions, task=task, job=job, next_run=next_run,
                          capacity=capacity, enqueue=enqueue_mock)
    await engine.dispose()


def test_capacity_reason_rejects_manual_review_and_keeps_original_quorum():
    assert module.capacity_blocked_panel(metadata()) == 3
    assert module.capacity_blocked_panel(metadata(panel=5)) == 5
    for change in ("voted", "abstained", "reason", "documents", "quorum", "revision", "capture", "mixed"):
        value = metadata()
        province = value["province_ingestion"]
        audit = province["source_audit"]["image_reviews"][0]["audit"]
        if change in {"voted", "abstained"}:
            audit["readings"][0]["status"] = change
        elif change == "reason":
            audit["readings"][0]["reason"] = "preflight_failed"
        elif change == "documents":
            audit["documents"] = []
        elif change == "quorum":
            audit["quorum"] = 1
        elif change == "revision":
            province["revision_candidates"] = [{"reason": "protected_fact"}]
        elif change == "capture":
            province["source_audit"]["errors"] = [{"status": "image_capture_failed"}]
        else:
            other = deepcopy(province["source_audit"]["image_reviews"][0])
            other["audit"]["readings"][0]["status"] = "abstained"
            province["source_audit"]["image_reviews"].append(other)
        assert module.capacity_blocked_panel(value) is None, change


def test_persisted_budget_and_backoff_do_not_depend_on_service_memory():
    task = source_task()
    task.completed_at = NOW
    assert module.recovery_candidate(task, now=NOW + timedelta(seconds=299)) is None
    first = module.recovery_candidate(task, now=NOW + timedelta(seconds=300))
    assert first["attempt"] == 1
    task.input_data = {**task.input_data, module.RECOVERY_KEY: first}
    assert module.recovery_candidate(task, now=NOW + timedelta(seconds=599)) is None
    second = module.recovery_candidate(task, now=NOW + timedelta(seconds=600))
    assert second["attempt"] == 2
    task.input_data = {**task.input_data, module.RECOVERY_KEY: second}
    assert module.recovery_candidate(task, now=NOW + timedelta(days=30)) is None


async def test_enqueues_once_with_quorum_then_observes_persisted_child(env):
    queued = await module.recover_pending_image_reviews([env.job], now=NOW)
    assert len(queued) == 1
    assert await module.recover_pending_image_reviews([env.job], now=NOW) == []
    env.enqueue.assert_awaited_once()
    plan = env.enqueue.call_args.kwargs["metadata"][module.RECOVERY_KEY]
    assert plan == {"attempt": 1, "max_attempts": 2, "root_task_uuid": env.task.task_uuid,
                    "source_task_uuid": env.task.task_uuid, "panel_size": 3, "source_attempt": 1}
    async with env.sessions() as db:
        state = (await db.execute(select(ScheduledJobState))).scalar_one()
        assert state.next_run_at == env.next_run
        assert state.last_task_uuid == queued[0] and state.last_status == "queued"
        original = (await db.execute(select(Task).where(Task.task_uuid == env.task.task_uuid))).scalar_one()
        assert original.status == TaskStatus.COMPLETED
        assert original.metadata_ == metadata()


@pytest.mark.parametrize("change", ["disabled", "paused", "new_task", "rescheduled", "country_changed"])
async def test_rechecks_control_panel_and_latest_task_after_capacity_lookup(env, change):
    new_next = env.next_run + timedelta(days=1)

    async def changed_capacity(panel):
        async with env.sessions() as db:
            job = (await db.execute(select(AutomationJob))).scalar_one()
            state = (await db.execute(select(ScheduledJobState))).scalar_one()
            if change == "disabled":
                job.enabled = False
            elif change == "paused":
                state.next_run_at = None
            elif change == "rescheduled":
                state.next_run_at = new_next
            elif change == "country_changed":
                job.country_code = "CN-GS"
            else:
                newer = source_task()
                newer.created_at, newer.status = NOW, TaskStatus.QUEUED
                db.add(newer)
            await db.commit()
        return {"quorum_available": True}

    env.capacity.side_effect = changed_capacity
    result = await module.recover_pending_image_reviews([env.job], now=NOW)
    assert bool(result) is (change == "rescheduled")
    if change == "rescheduled":
        async with env.sessions() as db:
            assert (await db.execute(select(ScheduledJobState))).scalar_one().next_run_at == new_next
    else:
        env.enqueue.assert_not_awaited()


async def test_capacity_not_ready_or_country_busy_does_not_consume_budget(env):
    env.capacity.return_value = {"quorum_available": False}
    assert await module.recover_pending_image_reviews([env.job], now=NOW) == []
    env.enqueue.assert_not_awaited()
    env.capacity.return_value = {"quorum_available": True}
    env.enqueue.side_effect = None
    env.enqueue.return_value = SimpleNamespace(created=False, task=SimpleNamespace(task_uuid="other-country-task"))
    assert await module.recover_pending_image_reviews([env.job], now=NOW) == []
    async with env.sessions() as db:
        state = (await db.execute(select(ScheduledJobState))).scalar_one()
        assert state.next_run_at == env.next_run and state.last_task_uuid == env.task.task_uuid


async def test_later_incomplete_attempt_is_not_recovered_from_old_success(env):
    async with env.sessions() as db:
        db.add(RunStage(task_uuid=env.task.task_uuid, stage="collect", attempt=2,
                       status="completed", started_at=NOW - timedelta(minutes=10)))
        await db.commit()
    assert await module.recover_pending_image_reviews([env.job], now=NOW) == []
    env.enqueue.assert_not_awaited()


async def test_current_job_can_disable_processing_of_recovered_images(env):
    async with env.sessions() as db:
        job = (await db.execute(select(AutomationJob))).scalar_one()
        job.process = False
        await db.commit()
    assert len(await module.recover_pending_image_reviews([env.job], now=NOW)) == 1
    assert env.enqueue.call_args.kwargs["process"] is False


async def test_second_capacity_failure_uses_persisted_budget_then_exhausts(env):
    [first] = await module.recover_pending_image_reviews([env.job], now=NOW)

    async def complete_pending(task_uuid, when):
        async with env.sessions() as db:
            task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one()
            task.status, task.completed_at, task.metadata_ = TaskStatus.COMPLETED, when, metadata()
            for name in ("collect", "organize"):
                db.add(RunStage(task_uuid=task_uuid, stage=name, attempt=1,
                               status="completed", started_at=when, finished_at=when))
            await db.commit()

    await complete_pending(first, NOW)
    assert await module.recover_pending_image_reviews([env.job], now=NOW + timedelta(minutes=9)) == []
    [second] = await module.recover_pending_image_reviews([env.job], now=NOW + timedelta(minutes=10))
    second_plan = env.enqueue.call_args.kwargs["metadata"][module.RECOVERY_KEY]
    assert second_plan["attempt"] == 2 and second_plan["root_task_uuid"] == env.task.task_uuid
    await complete_pending(second, NOW + timedelta(minutes=10))
    assert await module.recover_pending_image_reviews([env.job], now=NOW + timedelta(days=1)) == []
    assert env.enqueue.await_count == 2


@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.RUNNING])
async def test_failed_or_cancelled_recovery_does_not_repeat_every_tick(env, status):
    [child_uuid] = await module.recover_pending_image_reviews([env.job], now=NOW)
    async with env.sessions() as db:
        child = (await db.execute(select(Task).where(Task.task_uuid == child_uuid))).scalar_one()
        child.status = status
        child.metadata_, child.completed_at = metadata(), NOW
        await db.commit()
    assert await module.recover_pending_image_reviews([env.job], now=NOW + timedelta(hours=1)) == []
    env.enqueue.assert_awaited_once()


async def archive(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plan = {"country_code": "CN-SN", "source": module.SOURCE, "reports": [], "errors": []}
    payloads = [("province_plan", json.dumps(plan).encode()), ("province_response", b"original image bytes")]
    artifacts = []
    async with env.sessions() as db:
        for number, (kind, payload) in enumerate(payloads):
            path = Path("data/raw/cn-sn") / f"original-{number}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifact = SourceArtifact(task_uuid=env.task.task_uuid, attempt=1, relative_path=str(path),
                source_url=f"https://official.example/{number}", kind=kind, sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload), created_at=NOW - timedelta(minutes=31),
                metadata_={"media_type": "image/png", "status_code": 200})
            artifacts.append(artifact)
            db.add(artifact)
        stage = (await db.execute(select(RunStage).where(RunStage.stage == "collect"))).scalar_one()
        stage.metrics = {"artifact_count": len(artifacts)}
        await db.commit()
    recovery = {**module.recovery_candidate(env.task, now=NOW), "source_attempt": 1}
    writer = SimpleNamespace(task_uuid=str(uuid4()), artifacts=[])

    async def write(payload, **kwargs):
        writer.artifacts.append({"payload": payload, **kwargs})
    writer.write = AsyncMock(side_effect=write)
    return artifacts, recovery, writer


async def test_recovery_collection_uses_checked_archive_without_network(env, tmp_path, monkeypatch):
    artifacts, recovery, writer = await archive(env, tmp_path, monkeypatch)
    monkeypatch.setattr(connector, "_collect", lambda *args, **kwargs: pytest.fail("no refetch during recovery"))
    result = await connector.collect(writer, source=module.SOURCE, country_code="CN-SN",
                                     image_recovery=recovery, image_panel_size=3)
    assert [entry["payload"] for entry in result] == [module.read_artifact(a) for a in artifacts]
    assert all(entry["metadata"]["image_recovery"]["source_task_uuid"] == env.task.task_uuid for entry in result)
    assert all(entry["metadata"]["status_code"] == 200 for entry in result)


@pytest.mark.parametrize("problem", ["digest", "count", "scope", "attempt", "panel", "lineage"])
async def test_recovery_fails_closed_for_damaged_or_mismatched_archive(env, tmp_path, monkeypatch, problem):
    artifacts, recovery, writer = await archive(env, tmp_path, monkeypatch)
    code = "CN-SN"
    if problem == "digest":
        Path(artifacts[0].relative_path).write_bytes(b"tampered plan")
    elif problem == "count":
        async with env.sessions() as db:
            stage = (await db.execute(select(RunStage).where(RunStage.stage == "collect"))).scalar_one()
            stage.metrics = {"artifact_count": 3}
            await db.commit()
    elif problem == "scope":
        code = "CN-GS"
    elif problem == "attempt":
        recovery["source_attempt"] = 2
    elif problem == "panel":
        recovery["panel_size"] = 5
    else:
        recovery["attempt"] = 2
    with pytest.raises(ValueError):
        await module.copy_recovery_artifacts(writer, recovery=recovery, country_code=code, source=module.SOURCE)
    writer.write.assert_not_awaited()
