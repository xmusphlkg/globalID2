"""Verify progress projections against real parent-row contention in PostgreSQL."""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai.runtime import dispatcher, progress, store
from src.domain import AIWorkItem, Base, Task, TaskStatus, TaskType, TaskWorkbook


@pytest.fixture
async def database(monkeypatch):
    url = os.environ.get("TEST_AI_DATABASE_URL")
    if not url:
        pytest.skip("TEST_AI_DATABASE_URL must be a disposable PostgreSQL database")
    schema = "ai_progress_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        pool_size=4,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    tables = {Task.__table__, TaskWorkbook.__table__, AIWorkItem.__table__}
    while True:
        expanded = tables | {
            fk.column.table for table in tables for fk in table.foreign_keys
        }
        if expanded == tables:
            break
        tables = expanded
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with sessions.begin() as db:
            yield db

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=list(tables))
            )
        for module in (progress, dispatcher, store):
            monkeypatch.setattr(module, "get_database", factory)
        async with factory() as db:
            db.add(
                Task(
                    task_uuid="parent",
                    task_name="Progress contention",
                    task_type=TaskType.AGENT_WORKFLOW,
                    status=TaskStatus.RUNNING,
                    metadata_={"ai_execution_generation": 1},
                    progress=0,
                )
            )
        yield factory, engine
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


def success(generation=1):
    return {
        "parent": "parent",
        "execution_generation": generation,
        "status": "succeeded",
        "record_error": False,
    }


async def add_work(factory, *, generation=1):
    async with factory() as db:
        return await store.submit_activity(
            db,
            parent_task_uuid="parent",
            line="content_governance",
            priority="normal",
            payload={"execution_generation": generation},
        )


async def test_locked_business_parent_does_not_block_success_projection(database):
    factory, _engine = database
    async with factory() as db:
        for status in ("succeeded", "queued"):
            db.add(
                AIWorkItem(
                    parent_task_uuid="parent",
                    line="content_governance",
                    priority="normal",
                    status=status,
                    payload={"execution_generation": 1},
                )
            )
    buffer = progress.SuccessProgressBuffer()
    buffer.mark(success())
    async with factory() as lock:
        await lock.scalar(
            select(Task).where(Task.task_uuid == "parent").with_for_update()
        )
        async with asyncio.timeout(0.5):
            await buffer.flush()
        assert len(buffer._pending) == 1
    await buffer.flush(force=True)
    assert not buffer._pending
    async with factory() as db:
        assert await db.scalar(select(Task.progress)) == 50


async def test_success_finishes_and_releases_permit_while_parent_is_locked(database):
    factory, _engine = database
    item = await add_work(factory)
    route = {
        "model_key": "provider:model",
        "runtime_model_capacity": 1,
        "available_for_routing": True,
    }
    leased = (await store.claim_work_batch(route, limit=1, owner="executor"))[0]

    async def complete(*args):
        return "done"

    worker = dispatcher.AIWorkDispatcher(complete=complete, owner="executor")
    try:
        async with factory() as lock:
            await lock.scalar(
                select(Task).where(Task.task_uuid == "parent").with_for_update()
            )
            async with asyncio.timeout(0.5):
                await worker._execute(leased, route)
            assert len(worker.progress._pending) == 1
            async with factory() as db:
                current = await db.get(AIWorkItem, item.id)
                assert current.status == "succeeded"
                assert current.lease_owner is None
                assert current.lease_expires_at is None
        await worker.progress.flush(force=True)
        async with factory() as db:
            assert await db.scalar(select(Task.progress)) == 99
    finally:
        await worker.transport.close()


async def test_burst_uses_one_count_and_old_generation_cannot_overwrite_retry(database):
    factory, engine = database
    async with factory() as db:
        db.add_all(
            [
                AIWorkItem(
                    parent_task_uuid="parent",
                    line="content_governance",
                    priority="normal",
                    status="succeeded",
                    payload={"execution_generation": 0},
                ),
                AIWorkItem(
                    parent_task_uuid="parent",
                    line="content_governance",
                    priority="normal",
                    status="queued",
                    payload={"execution_generation": 1},
                ),
            ]
        )
    aggregates = []

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def count(_conn, _cursor, statement, _params, _context, _many):
        if "GROUP BY ai_work_items.status" in statement:
            aggregates.append(statement)

    buffer = progress.SuccessProgressBuffer()
    for _ in range(500):
        buffer.mark(success(0))
    await buffer.flush()
    assert not aggregates
    assert not buffer._pending
    for _ in range(500):
        buffer.mark(success(1))
    await buffer.flush(force=True)
    assert len(aggregates) == 1
    async with factory() as db:
        assert await db.scalar(select(Task.progress)) == 0


async def test_failure_history_is_not_coalesced_or_lost_across_generations(database):
    factory, _engine = database
    async with factory() as db:
        db.add(
            AIWorkItem(
                parent_task_uuid="parent",
                line="content_governance",
                priority="normal",
                status="queued",
                payload={"execution_generation": 1},
            )
        )
    for work_id, generation in [(1, 0), (2, 1)]:
        await progress.note_parent_work(
            {
                "parent": "parent",
                "execution_generation": generation,
                "work_id": work_id,
                "line": "content_governance",
                "attempt": 2,
                "status": "queued",
                "record_error": True,
                "error": "test failure",
            }
        )
    async with factory() as db:
        history = (
            await db.scalars(select(TaskWorkbook).order_by(TaskWorkbook.id))
        ).all()
        assert len(history) == 2
        assert [row.metadata_["execution_generation"] for row in history] == [0, 1]
        assert all(row.error_message == "test failure" for row in history)
        assert await db.scalar(select(Task.progress)) == 0


async def test_locked_parent_failure_burst_does_not_exhaust_pool(database):
    factory, engine = database
    worker = dispatcher.AIWorkDispatcher(owner="failure-writer")
    events = [
        {"parent": "parent", "execution_generation": 1, "work_id": i,
         "line": "content_governance", "attempt": 1, "status": "failed",
         "record_error": True, "error": "provider failed"}
        for i in range(6)
    ]
    pending = []
    try:
        async with factory() as lock:
            await lock.scalar(select(Task).where(Task.task_uuid == "parent").with_for_update())
            pending = [asyncio.create_task(worker._note_failure_progress(event)) for event in events]
            async with asyncio.timeout(2):
                while engine.pool.checkedout() < 2:
                    await asyncio.sleep(0.01)
                # Allow queued writers to attempt entry while the first one is blocked.
                await asyncio.sleep(0.05)
                assert engine.pool.checkedout() == 2
                async with factory() as independent:
                    assert await independent.scalar(select(Task.id).where(Task.task_uuid == "parent"))
        async with asyncio.timeout(5):
            await asyncio.gather(*pending)
        async with factory() as db:
            history = (await db.scalars(select(TaskWorkbook))).all()
            assert {row.metadata_["work_id"] for row in history} == set(range(6))
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await worker.transport.close()
