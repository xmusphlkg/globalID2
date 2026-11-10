"""Recovery operations remain reachable without opening unbounded AI parents."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")

from src.domain import Base, Task, TaskPriority, TaskStatus, TaskType
from src.services import task_worker as worker


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    url = os.environ.get("TEST_TASK_WORKER_DATABASE_URL")
    schema = f"worker_test_{uuid4().hex}" if url else None
    if schema:
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'organizers.db'}")
    tables = [Task.__table__]
    for table in tables:
        for foreign_key in table.foreign_keys:
            referenced = foreign_key.column.table
            if referenced not in tables:
                tables.append(referenced)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_database():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    monkeypatch.setattr(worker, "get_database", get_database)
    yield maker
    worker._claimed_health_operation_uuids.clear()
    worker._claimed_collection_only_situation_uuids.clear()
    worker._claimed_raw_archive_uuids.clear()
    await engine.dispose()
    if schema:
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def task(uuid, task_type, operation=None):
    return Task(task_uuid=uuid, task_type=task_type, task_name=uuid,
                status=TaskStatus.QUEUED, priority=TaskPriority.NORMAL,
                input_data={"operation": operation} if operation else {})


async def test_no_routes_pauses_business_but_health_and_general_work_can_run(database):
    async with database() as db:
        db.add_all([
            task("review", TaskType.AGENT_WORKFLOW, "governance_stream"),
            task("report", TaskType.GENERATE_REPORT),
            task("health-1", TaskType.AGENT_WORKFLOW, "check_model"),
            task("health-2", TaskType.AGENT_WORKFLOW, "check_all_models"),
            task("collection", TaskType.CRAWL_DATA),
        ])
        await db.commit()

    blocked = worker._lane_blocked_task_types([], ai_capacity=0)
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) == (
        "health-1", TaskType.AGENT_WORKFLOW
    )
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) == (
        "collection", TaskType.CRAWL_DATA
    )
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) is None

    async with database() as db:
        health = (await db.execute(select(Task).where(Task.task_uuid == "health-1"))).scalar_one()
        health.status = TaskStatus.COMPLETED
        await db.commit()
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) == (
        "health-2", TaskType.AGENT_WORKFLOW
    )


async def test_full_business_lanes_still_admit_only_one_health_parent(database):
    async with database() as db:
        db.add_all([
            task("health-1", TaskType.AGENT_WORKFLOW, "check_provider"),
            task("health-2", TaskType.AGENT_WORKFLOW, "check_model"),
            task("ordinary-agent", TaskType.AGENT_WORKFLOW),
        ])
        await db.commit()
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=set(TaskType)) == (
        "health-1", TaskType.AGENT_WORKFLOW
    )
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=set(TaskType)) is None


async def test_non_health_agent_without_operation_is_not_filtered_out(database):
    async with database() as db:
        db.add(task("ordinary-agent", TaskType.AGENT_WORKFLOW))
        await db.commit()
    assert await worker._claim_next_task_uuid("worker") == ("ordinary-agent", TaskType.AGENT_WORKFLOW)


async def test_situation_ai_is_bounded_but_collection_only_uses_ordinary_lane(database):
    collection = task("collect-only", TaskType.REFRESH_SITUATION_SOURCES)
    collection.input_data = {"skip_ai": True}
    async with database() as db:
        db.add_all([task("extract", TaskType.REFRESH_SITUATION_SOURCES), collection])
        await db.commit()
    blocked = worker._lane_blocked_task_types([], ai_capacity=0)
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) == (
        "collect-only", TaskType.REFRESH_SITUATION_SOURCES,
    )
    assert "collect-only" in worker._claimed_collection_only_situation_uuids
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) is None


async def test_collection_only_does_not_bypass_a_full_ordinary_lane(database):
    collection = task("collect-only", TaskType.REFRESH_SITUATION_SOURCES)
    collection.input_data = {"skip_ai": True}
    async with database() as db:
        db.add_all([collection, task("extract", TaskType.REFRESH_SITUATION_SOURCES)])
        await db.commit()
    blocked = worker._lane_blocked_task_types([], ai_capacity=2, general_capacity=1, collection_only_count=1)
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) == (
        "extract", TaskType.REFRESH_SITUATION_SOURCES,
    )
    assert await worker._claim_next_task_uuid("worker", blocked_task_types=blocked) is None


@pytest.mark.parametrize("role", ["general", "ai"])
async def test_role_filter_partitions_tasks_even_when_lane_overrides_apply(database, role):
    collection = task("collection-only", TaskType.REFRESH_SITUATION_SOURCES)
    collection.input_data = {"skip_ai": True}
    archive = task("archive", TaskType.EXPORT_DATA)
    archive.input_data = {"raw_archive_only": True}
    queued = [
        task("health", TaskType.AGENT_WORKFLOW, "check_model"),
        task("review", TaskType.AGENT_WORKFLOW, "governance_stream"),
        task("extract", TaskType.REFRESH_SITUATION_SOURCES),
        task("crawl", TaskType.CRAWL_DATA), collection, archive,
    ]
    async with database() as db:
        db.add_all(queued)
        await db.commit()
    # Both health and archival bypass ordinary capacity gates; role is final.
    result = await worker._claim_next_task_uuid(role, role=role, blocked_task_types=set(TaskType))
    assert result == (("health", TaskType.AGENT_WORKFLOW) if role == "ai" else ("archive", TaskType.EXPORT_DATA))
    assert await worker._claim_next_task_uuid(role, role=role, blocked_task_types=set(TaskType)) is None
    found = set()
    while claimed := await worker._claim_next_task_uuid(role, role=role):
        found.add(claimed[0])
    assert found == ({"review", "extract"} if role == "ai" else {"crawl", "collection-only"})


@pytest.mark.parametrize("skip_ai", [None, False, True])
async def test_situation_role_boundary_uses_explicit_true_only(database, skip_ai):
    row = task("situation", TaskType.REFRESH_SITUATION_SOURCES)
    row.input_data = {} if skip_ai is None else {"skip_ai": skip_ai}
    async with database() as db:
        db.add(row)
        await db.commit()
    expected = "general" if skip_ai is True else "ai"
    wrong = "ai" if expected == "general" else "general"
    assert await worker._claim_next_task_uuid(wrong, role=wrong) is None
    assert await worker._claim_next_task_uuid(expected, role=expected) == ("situation", TaskType.REFRESH_SITUATION_SOURCES)


async def test_live_worker_process_leases_protect_stale_task_heartbeats(database, monkeypatch):
    from src.services import task_executor

    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    async with database() as db:
        for role, task_type in (("general", TaskType.CRAWL_DATA), ("ai", TaskType.ENRICH_LITERATURE)):
            row = task(role, task_type)
            row.status = TaskStatus.RUNNING
            row.metadata_ = {"task_lease": {"owner": f"live-{role}", "heartbeat_at": stale.isoformat()}}
            db.add(row)
        dead = task("dead", TaskType.ENRICH_LITERATURE)
        dead.status = TaskStatus.RUNNING
        dead.metadata_ = {"task_lease": {"owner": "dead-worker", "heartbeat_at": stale.isoformat()}}
        db.add(dead)
        await db.commit()
    monkeypatch.setattr(task_executor, "get_database", worker.get_database)
    monkeypatch.setattr(task_executor.task_manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(task_executor.task_manager, "_broadcast", AsyncMock())
    owners = {"worker": "live-general", "ai-worker": "live-ai"}
    monkeypatch.setattr(worker, "runtime_registry", SimpleNamespace(
        lease_owner=AsyncMock(side_effect=lambda service: (owners[service], True)),
    ))
    assert await worker._recover_orphaned_tasks("starting-worker") == 1
    async with database() as db:
        states = dict((await db.execute(select(Task.task_uuid, Task.status))).all())
    assert states == {"general": TaskStatus.RUNNING, "ai": TaskStatus.RUNNING, "dead": TaskStatus.QUEUED}


async def test_registry_unavailable_defers_task_recovery(monkeypatch):
    recover = AsyncMock()
    monkeypatch.setattr(worker, "recover_interrupted_tasks_on_startup", recover)
    monkeypatch.setattr(worker, "runtime_registry", SimpleNamespace(
        lease_owner=AsyncMock(side_effect=[("other-live-worker", True), (None, False)]),
    ))
    assert await worker._recover_orphaned_tasks("this-worker") == 0
    recover.assert_not_awaited()


async def test_postgres_cross_role_disease_admission_is_serialized(database, monkeypatch):
    if database.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL advisory/row-lock concurrency check")
    async with database() as db:
        author = task("knowledge", TaskType.UPDATE_DISEASE_KNOWLEDGE)
        sources = task("sources", TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES)
        author.input_data = sources.input_data = {"disease_id": "influenza"}
        db.add_all([author, sources])
        await db.commit()
    lock_acquired, release_first = asyncio.Event(), asyncio.Event()
    paused = False

    @asynccontextmanager
    async def pausing_database():
        nonlocal paused
        async with database() as db:
            scalar = db.scalar
            async def pause_after_claim_lock(statement, *args, **kwargs):
                nonlocal paused
                value = await scalar(statement, *args, **kwargs)
                if "pg_try_advisory_xact_lock" in str(statement) and value and not paused:
                    paused = True
                    lock_acquired.set()
                    await release_first.wait()
                return value
            db.scalar = pause_after_claim_lock
            yield db
            await db.commit()

    monkeypatch.setattr(worker, "get_database", pausing_database)
    first = asyncio.create_task(worker._claim_next_task_uuid("ai", role="ai"))
    try:
        await asyncio.wait_for(lock_acquired.wait(), 2)
        # A different row and role still competes for the same disease resource.
        assert await asyncio.wait_for(worker._claim_next_task_uuid("general", role="general"), 2) is None
    finally:
        release_first.set()
    assert await asyncio.wait_for(first, 2) == ("knowledge", TaskType.UPDATE_DISEASE_KNOWLEDGE)
    assert await worker._claim_next_task_uuid("general", role="general") is None
    async with database() as db:
        current = await db.scalar(select(Task).where(Task.task_uuid == "knowledge"))
        current.status = TaskStatus.COMPLETED
        await db.commit()
    assert await worker._claim_next_task_uuid("general", role="general") == ("sources", TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES)
