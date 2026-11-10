"""AI task progress and the log lines synthesized for the control panel."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai.runtime import progress as runtime_progress
from src.ai.task_progress import displayed_progress
from src.control_plane.ai_task_trace import (
    build_ai_task_entries,
    trace_for_task,
    work_counts,
)
from src.domain import Base, RunStage, Task, TaskStatus, TaskType, TaskWorkbook
from src.domain.ai_work_item import AIWorkItem


def _item(**kwargs):
    base = dict(
        id=7,
        line="agent_workflow",
        status="failed",
        attempt=2,
        route_key="kiro:hy3",
        error="TimeoutError: model timed out",
        payload={"agent_name": "content_governance_reviewer"},
        created_at=datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 31, 8, 1, tzinfo=timezone.utc),
        lease_expires_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_running_agent_workflow_uses_model_call_progress():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 8, "leased": 2, "queued": 10},
        )
        == 40
    )


def test_running_agent_workflow_does_not_show_complete_before_the_parent_finishes():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 4},
        )
        == 99
    )


def test_completed_model_operation_without_stored_progress_reads_as_done():
    assert (
        displayed_progress(
            task_type="AGENT_WORKFLOW",
            status="COMPLETED",
            stored=0,
            counts={},
        )
        == 100
    )


def test_completed_json_release_reads_as_done_but_failed_release_retains_progress():
    assert displayed_progress(task_type="export_data", status="completed", stored=65) == 100
    assert displayed_progress(task_type="export_data", status="failed", stored=65) == 65


def test_literature_progress_already_recorded_by_the_service_stays_put():
    assert (
        displayed_progress(
            task_type="enrich_literature",
            status="running",
            stored=80,
            counts={"succeeded": 1, "queued": 9},
        )
        == 80
    )


def test_failed_knowledge_reconcile_uses_stage_progress_when_no_model_call_ran():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="failed",
            stored=0,
            counts={},
            stages={"completed": 2, "failed": 1},
        )
        == 100
    )


def test_running_knowledge_reconcile_stops_short_of_100_until_the_parent_finishes():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={},
            stages={"completed": 1, "running": 1},
        )
        == 50
    )


def test_model_call_progress_wins_over_stage_rows():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 1, "queued": 3},
            stages={"completed": 3},
        )
        == 25
    )


def test_failed_agent_workflow_shows_how_far_the_model_calls_got():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="failed",
            stored=0,
            counts={"succeeded": 1, "failed": 1, "queued": 2},
        )
        == 50
    )


def test_trace_includes_stage_metrics_model_failures_and_result():
    now = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
    entries = build_ai_task_entries(
        task_type="agent_workflow",
        status="running",
        input_data={"operation": "governance_stream"},
        output_data={"result": {"claimed": 4, "published": 1, "rejected": 0, "deferred": 1}},
        completed_at=None,
        counts={"succeeded": 3, "failed": 1, "leased": 1},
        items=[
            _item(),
            _item(
                id=8,
                status="leased",
                error=None,
                finished_at=None,
                lease_expires_at=now + timedelta(minutes=10),
            ),
        ],
        stages=[
            SimpleNamespace(
                stage="store",
                status="completed",
                attempt=1,
                metrics={"source_slots": 6, "content_gap_created": 0},
                error=None,
                started_at=now - timedelta(seconds=30),
                finished_at=now - timedelta(seconds=5),
            ),
            SimpleNamespace(
                stage="collect",
                status="skipped",
                attempt=1,
                metrics={},
                error=None,
                started_at=now,
                finished_at=None,
            ),
        ],
        now=now,
    )
    titles = [entry["title"] for entry in entries]
    assert "Stage store completed" in titles
    assert "Stage collect skipped" not in titles
    assert any(entry["title"] == "Model call failed" and entry["entry_type"] == "error" for entry in entries)
    assert any(entry["title"] == "Model call leased" for entry in entries)
    progress = next(entry for entry in entries if entry["title"] == "Model work progress")
    assert "operation: governance_stream" in progress["content"]
    assert "failed: 1" in progress["content"]
    assert progress["entry_type"] == "warning"
    result = next(entry for entry in entries if entry["title"] == "Task result")
    assert "claimed: 4" in result["content"]
    stage = next(entry for entry in entries if entry["title"] == "Stage store completed")
    assert "source_slots: 6" in stage["content"]


def test_non_ai_task_without_model_work_adds_no_log():
    assert (
        build_ai_task_entries(
            task_type="crawl_data",
            status="completed",
            input_data={},
            output_data={"total_records": 3},
            completed_at=None,
            counts={},
            items=[],
            stages=[],
        )
        == []
    )


@pytest_asyncio.fixture
async def trace_database(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            Task.__table__, TaskWorkbook.__table__, AIWorkItem.__table__, RunStage.__table__,
        ]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions.begin() as session:
            yield session

    monkeypatch.setattr(runtime_progress, "get_database", database)
    yield sessions
    await engine.dispose()


async def _retry_trace_fixture(database):
    async with database.begin() as session:
        task = Task(task_uuid="same-parent", task_name="review", task_type=TaskType.AGENT_WORKFLOW,
                    status=TaskStatus.RUNNING, input_data={}, metadata_={"ai_execution_generation": 1})
        session.add(task)
        old_failed = AIWorkItem(parent_task_uuid=task.task_uuid, line="content_governance", priority="normal",
                                status="failed", payload={}, error="old upstream failure")
        old_success = AIWorkItem(parent_task_uuid=task.task_uuid, line="content_governance", priority="normal",
                                 status="succeeded", payload={"execution_generation": 0})
        current = AIWorkItem(parent_task_uuid=task.task_uuid, line="content_governance", priority="normal",
                             status="queued", payload={"execution_generation": 1})
        session.add_all([old_failed, old_success, current])
    return task, old_failed, old_success, current


async def test_retried_parent_counts_only_current_generation_but_keeps_history(trace_database):
    task, old_failed, old_success, current = await _retry_trace_fixture(trace_database)
    async with trace_database() as session:
        counts = await work_counts(session, [task.task_uuid])
        assert counts == {task.task_uuid: {"queued": 1}}
        progress, entries = await trace_for_task(session, await session.get(Task, task.id))
    assert progress == 0
    summary = next(entry for entry in entries if entry["title"] == "Model work progress")
    assert "failed: 0" in summary["content"] and "finished: 0/1" in summary["content"]
    assert summary["entry_type"] == "info"
    historical = [entry for entry in entries if entry["metadata"].get("execution_generation") == 0]
    assert {entry["metadata"]["work_id"] for entry in historical} == {old_failed.id, old_success.id}


async def test_late_old_event_does_not_advance_new_generation_progress(trace_database):
    task, old_failed, old_success, current = await _retry_trace_fixture(trace_database)
    event = runtime_progress._work_event(old_failed, route_key="old:model", record_error=True)
    assert event["execution_generation"] == 0
    await runtime_progress.note_parent_work(event)
    async with trace_database() as session:
        parent = await session.get(Task, task.id)
        assert parent.progress == 0
        history = (await session.scalars(select(TaskWorkbook))).all()
        assert len(history) == 1
        assert history[0].metadata_["execution_generation"] == 0
        assert history[0].error_message == "old upstream failure"
    async with trace_database.begin() as session:
        active = await session.get(AIWorkItem, current.id)
        active.status = "succeeded"
        session.add(AIWorkItem(parent_task_uuid=task.task_uuid, line="content_governance", priority="normal",
                               status="queued", payload={"execution_generation": 1}))
    current.status = "succeeded"
    await runtime_progress.note_parent_work(runtime_progress._work_event(current, route_key="new:model", record_error=False))
    async with trace_database() as session:
        assert (await session.get(Task, task.id)).progress == 50
        assert await work_counts(session, [task.task_uuid]) == {task.task_uuid: {"queued": 1, "succeeded": 1}}


async def test_generation_zero_counts_activities_without_generation_metadata(trace_database):
    async with trace_database.begin() as session:
        task = Task(task_uuid="original-parent", task_name="first run", task_type=TaskType.AGENT_WORKFLOW,
                    status=TaskStatus.RUNNING, input_data={})
        session.add(task)
        session.add(AIWorkItem(parent_task_uuid=task.task_uuid, line="content_governance", priority="normal",
                               status="queued", payload={}))
    async with trace_database() as session:
        assert await work_counts(session, [task.task_uuid]) == {task.task_uuid: {"queued": 1}}


async def test_history_remains_visible_before_new_generation_submits_work(trace_database):
    task, old_failed, old_success, current = await _retry_trace_fixture(trace_database)
    async with trace_database.begin() as session:
        await session.delete(await session.get(AIWorkItem, current.id))
    async with trace_database() as session:
        progress, entries = await trace_for_task(session, await session.get(Task, task.id))
    assert progress == 0
    assert {entry["metadata"].get("work_id") for entry in entries} >= {old_failed.id, old_success.id}
    assert not any(entry["title"] == "Model work progress" for entry in entries)
