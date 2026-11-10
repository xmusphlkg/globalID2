"""Recovery operations remain reachable without opening unbounded AI parents."""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")

from src.domain import Base, Task, TaskPriority, TaskStatus, TaskType
from src.services import task_worker as worker


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'organizers.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Task.__table__]))
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
    await engine.dispose()


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
