from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.control_plane.overview import ControlPlaneOverviewService
from src.domain import Base, Task, TaskStatus, TaskType
from src.services.data_release.realtime_monitor import recent_failure_clause, task_channel_clause


@pytest_asyncio.fixture
async def task_store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Task.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions() as session:
            yield session

    module = import_module("src.core.task_manager")
    monkeypatch.setattr(module, "get_db", database)
    yield sessions, module.TaskManager()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", [TaskType.EXPORT_DATA, TaskType.CRAWL_DATA, TaskType.AGENT_WORKFLOW])
async def test_success_commits_and_broadcasts_full_progress_and_ignores_late_updates(task_store, task_type):
    sessions, manager = task_store
    async with sessions() as db:
        db.add(Task(task_uuid="work-1", task_name="Work", task_type=task_type,
                    status=TaskStatus.RUNNING, progress=65, started_at=datetime.now(timezone.utc)))
        await db.commit()
    events = []

    async def capture(event):
        events.append(event)

    manager.set_broadcast_hook(capture)
    await manager.update_task_status("work-1", TaskStatus.COMPLETED)
    await manager.update_task_progress("work-1", 70)
    async with sessions() as db:
        saved = (await db.execute(select(Task).where(Task.task_uuid == "work-1"))).scalar_one()
        assert saved.status == TaskStatus.COMPLETED
        assert saved.progress == 100
    assert events[-1]["status"] == "completed" and events[-1]["progress"] == 100
    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_failure_and_cancellation_retain_their_last_real_progress(task_store, status):
    sessions, manager = task_store
    async with sessions() as db:
        db.add(Task(task_uuid="work-2", task_name="Work", task_type=TaskType.EXPORT_DATA,
                    status=TaskStatus.RUNNING, progress=65, started_at=datetime.now(timezone.utc)))
        await db.commit()
    await manager.update_task_status("work-2", status)
    await manager.update_task_progress("work-2", 95)
    async with sessions() as db:
        saved = (await db.execute(select(Task).where(Task.task_uuid == "work-2"))).scalar_one()
        assert saved.status == status and saved.progress == 65


@pytest.mark.asyncio
async def test_overview_displays_old_completed_rows_as_complete_without_changing_history(task_store):
    sessions, _ = task_store
    async with sessions() as db:
        db.add_all([
            Task(task_uuid="old-success", task_name="JSON release", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.COMPLETED, progress=65),
            Task(task_uuid="old-failure", task_name="Failed JSON", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.FAILED, progress=65),
        ])
        await db.commit()
        rows = await ControlPlaneOverviewService(db).recent_tasks()
        by_id = {row["task_uuid"]: row for row in rows}
        assert by_id["old-success"]["progress"] == 100
        assert by_id["old-failure"]["progress"] == 65
        stored = (await db.execute(select(Task).where(Task.task_uuid == "old-success"))).scalar_one()
        assert stored.progress == 65


@pytest.mark.asyncio
async def test_recent_failure_count_uses_failure_time_not_a_later_metadata_update(task_store):
    sessions, _ = task_store
    now = datetime(2026, 11, 7, 6, tzinfo=timezone.utc)
    async with sessions() as db:
        for ident, status, completed, updated in (
            ("old-failure", TaskStatus.FAILED, now - timedelta(days=2), now),
            ("recent-failure", TaskStatus.FAILED, now - timedelta(hours=1), now),
            ("legacy-failure", TaskStatus.FAILED, None, now),
            ("recent-success", TaskStatus.COMPLETED, now, now),
        ):
            db.add(Task(task_uuid=ident, task_name=ident, task_type=TaskType.EXPORT_DATA,
                input_data={"data_only": True, "data_line": "surveillance"}, status=status,
                completed_at=completed, updated_at=updated))
        await db.commit()
        count = (await db.execute(select(func.count(Task.id)).where(
            *task_channel_clause("surveillance"), recent_failure_clause(now)))).scalar_one()
        assert count == 2
