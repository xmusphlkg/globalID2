import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.core.release_admission import ReleaseAdmission, collection_database_scope
from src.domain import TaskStatus, TaskType
from src.services import task_executor


@pytest.mark.asyncio
async def test_release_task_runs_while_collection_commits(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'data.sqlite'}")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE observations (value INTEGER)"))
    monkeypatch.setattr(database, "_session_maker", async_sessionmaker(engine))
    gate = ReleaseAdmission()
    monkeypatch.setattr(task_executor, "release_admission", gate)
    tasks = {
        name: SimpleNamespace(task_uuid=name, task_type=kind, status=TaskStatus.RUNNING, input_data={})
        for name, kind in [("writer", TaskType.CRAWL_DATA), ("release", TaskType.EXPORT_DATA)]
    }
    monkeypatch.setattr(task_executor.task_manager, "get_task_by_uuid", AsyncMock(side_effect=tasks.get))
    monkeypatch.setattr(task_executor.task_manager, "is_cancel_requested", AsyncMock(return_value=False))
    writing, commit, publishing, finish_release = (asyncio.Event() for _ in range(4))

    async def dispatch(task):
        if task.task_type == TaskType.CRAWL_DATA:
            async with database.get_db() as db:
                await db.execute(text("INSERT INTO observations VALUES (1)"))
                writing.set()
                await commit.wait()
            async with database.get_db() as db:
                await db.execute(text("INSERT INTO observations VALUES (2)"))
        else:
            assert not gate._publishing
            publishing.set()
            await finish_release.wait()
        return {"done": task.task_uuid}

    monkeypatch.setattr(task_executor, "_dispatch", dispatch)
    try:
        async with asyncio.timeout(5):
            async with asyncio.TaskGroup() as group:
                writer = group.create_task(task_executor.execute_task("writer"))
                await writing.wait()
                release = group.create_task(task_executor.execute_task("release"))
                await publishing.wait()
                commit.set()
                assert await writer == {"done": "writer"}
                assert not release.done()
                finish_release.set()
            async with database.get_db() as db:
                assert (await db.execute(text("SELECT value FROM observations ORDER BY value"))).scalars().all() == [1, 2]
            assert not gate.cooperative_tasks
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_publication_failure_or_cancellation_reopens_admission(failure):
    gate = ReleaseAdmission()
    entered = asyncio.Event()

    async def writer():
        with gate.writer("writer"):
            async with collection_database_scope():
                entered.set()

    async with asyncio.timeout(5):
        with pytest.raises(failure):
            async with gate.publication():
                work = asyncio.create_task(writer())
                await asyncio.sleep(0)
                assert not entered.is_set()
                raise failure()
        await work
        assert entered.is_set()


@pytest.mark.asyncio
async def test_cancelled_waiting_release_does_not_strand_writers():
    gate = ReleaseAdmission()

    async def release():
        async with gate.publication():
            pytest.fail("Release entered before the store finished")

    async with asyncio.timeout(5):
        with gate.writer("writer"):
            async with collection_database_scope():
                work = asyncio.create_task(release())
                await asyncio.sleep(0)
                work.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await work
            async with collection_database_scope():
                pass
        async with gate.publication():
            pass


@pytest.mark.asyncio
async def test_nested_database_scopes_can_drain_after_release_requests_admission():
    gate = ReleaseAdmission()
    publishing = asyncio.Event()

    async def release():
        async with gate.publication():
            publishing.set()

    async def nested():
        async with collection_database_scope():
            assert not publishing.is_set()

    async with asyncio.timeout(5):
        with gate.writer("writer"):
            async with collection_database_scope():
                work = asyncio.create_task(release())
                await asyncio.sleep(0)
                # Includes child work awaited while a parent session is open.
                await asyncio.create_task(nested())
                await nested()
            await work
        assert publishing.is_set()


@pytest.mark.asyncio
async def test_child_cannot_reuse_an_admission_after_parent_scope_closed():
    gate = ReleaseAdmission()
    start_child, entered = asyncio.Event(), asyncio.Event()

    async def child():
        await start_child.wait()
        async with collection_database_scope():
            entered.set()

    async with asyncio.timeout(5):
        with gate.writer("writer"):
            async with collection_database_scope():
                work = asyncio.create_task(child())
        async with gate.publication():
            start_child.set()
            await asyncio.sleep(0)
            assert not entered.is_set()
        await work
        assert entered.is_set()


@pytest.mark.asyncio
async def test_reports_only_does_not_pause_collection():
    gate = ReleaseAdmission()
    async with asyncio.timeout(5):
        async with gate.task_scope("reports", TaskType.EXPORT_DATA, reports_only=True):
            with gate.writer("writer"):
                async with collection_database_scope():
                    pass


@pytest.mark.asyncio
async def test_network_or_model_wait_does_not_hold_publication():
    gate = ReleaseAdmission()
    waiting, finish_network = asyncio.Event(), asyncio.Event()

    async def writer():
        async with gate.task_scope("knowledge", TaskType.UPDATE_DISEASE_KNOWLEDGE):
            waiting.set()
            await finish_network.wait()
            async with collection_database_scope():
                pass

    async with asyncio.timeout(5):
        async with asyncio.TaskGroup() as group:
            work = group.create_task(writer())
            await waiting.wait()
            async with gate.task_scope("release", TaskType.EXPORT_DATA):
                assert not work.done()
                finish_network.set()
                await work
                assert work.done()


@pytest.mark.asyncio
async def test_raw_archive_does_not_wait_for_or_block_database_publication():
    gate = ReleaseAdmission()
    async with asyncio.timeout(1):
        with gate.writer("crawl"):
            async with collection_database_scope():
                async with gate.task_scope("archive", TaskType.EXPORT_DATA, raw_archive_only=True):
                    assert gate._publishing is False
        async with gate.publication():
            async with gate.task_scope("archive", TaskType.EXPORT_DATA, raw_archive_only=True):
                assert gate._publishing is True


@pytest.mark.asyncio
async def test_store_rollback_and_close_finish_before_publication(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.sqlite'}")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE observations (value INTEGER)"))
    monkeypatch.setattr(database, "_session_maker", async_sessionmaker(engine))
    gate = ReleaseAdmission()

    async def release():
        async with gate.publication():
            async with database.get_db() as db:
                assert (await db.execute(text("SELECT COUNT(*) FROM observations"))).scalar_one() == 0

    # Start outside the writer context: release database operations bypass the
    # collection gate, just like the independently claimed worker coroutine.
    start = asyncio.Event()
    async def delayed_release():
        await start.wait()
        await release()

    work = asyncio.create_task(delayed_release())
    try:
        async with asyncio.timeout(5):
            with pytest.raises(RuntimeError):
                with gate.writer("writer"):
                    async with database.get_db() as db:
                        await db.execute(text("INSERT INTO observations VALUES (1)"))
                        start.set()
                        await asyncio.sleep(0)
                        raise RuntimeError("rollback store")
            await work
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await engine.dispose()
