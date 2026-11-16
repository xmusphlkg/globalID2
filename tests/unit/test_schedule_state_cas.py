"""Exercise next-run CAS against the actual SQL table, including NULLs."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.control_plane import schedule_state as module
from src.domain import ScheduledJobState


@pytest.fixture
async def repository(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ScheduledJobState.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(module, "get_database", database)
    yield module.ScheduleStateRepository(), sessions
    await engine.dispose()


async def row(sessions):
    async with sessions() as session:
        return (await session.execute(select(ScheduledJobState))).scalar_one()


async def test_reschedule_only_changes_next_and_initializer_never_overwrites(repository):
    repo, sessions = repository
    # SQLite stores this table's DateTime values without timezone offsets.
    old = datetime(2026, 11, 16, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    new = old + timedelta(minutes=30)
    await repo.reschedule("ingestion", "job", old)
    async with sessions() as session:
        state = (await session.execute(select(ScheduledJobState))).scalar_one()
        state.last_status = "queued"
        state.last_task_uuid = "previous-task"
        state.last_error = "preserve evidence"
        state.last_started_at = old - timedelta(minutes=2)
        state.last_finished_at = old - timedelta(minutes=1)
        await session.commit()
    previous = await row(sessions)
    await repo.reschedule("ingestion", "job", new)
    await repo.reschedule("ingestion", "job", old, only_if_missing=True)
    actual = await row(sessions)
    assert actual.next_run_at == new
    for field in ("last_status", "last_task_uuid", "last_error", "last_started_at", "last_finished_at"):
        assert getattr(actual, field) == getattr(previous, field)
    await repo.reschedule("ingestion", "job", None)
    await repo.reschedule("ingestion", "job", old, only_if_missing=True)
    assert (await row(sessions)).next_run_at is None


@pytest.mark.parametrize("expected_null", [False, True])
@pytest.mark.parametrize("concurrent_edit", ["none", "reschedule", "pause"])
async def test_sql_case_preserves_concurrent_panel_next_but_records_result(
    repository, expected_null, concurrent_edit,
):
    repo, sessions = repository
    now = datetime(2026, 11, 16, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    expected = None if expected_null else now
    await repo.reschedule("ingestion", "job", expected, only_if_missing=True)
    panel_next = now + timedelta(minutes=30)
    if concurrent_edit == "pause":
        # With an initial NULL, first move away so the pause is a real edit.
        if expected_null:
            expected = now
            await repo.reschedule("ingestion", "job", expected)
        panel_next = None
    if concurrent_edit != "none":
        await repo.reschedule("ingestion", "job", panel_next)

    result = SimpleNamespace(
        next_run_at=now + timedelta(days=1), last_status="queued",
        last_task_uuid="actual-dispatch", last_error=None,
        last_started_at=now, last_finished_at=now + timedelta(seconds=1),
    )
    saved_next = await repo.save_result(
        "ingestion", "job", result, expected_next_run_at=expected,
    )
    actual = await row(sessions)
    target = result.next_run_at if concurrent_edit == "none" else panel_next
    assert actual.next_run_at == saved_next == target
    assert actual.last_task_uuid == "actual-dispatch"
    assert actual.last_status == "queued"
    assert actual.last_finished_at == result.last_finished_at
