"""Generation and public-projection commits share one immutable worker interval."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Task, TaskStatus, TaskType
from src.services import task_worker
from src.services.task_worker import (
    COLLECTION_STORE_TASK_TYPES,
    _release_memory_blocked_task_types,
)


@pytest.mark.parametrize(
    "store_type",
    [
        TaskType.CRAWL_DATA,
        TaskType.SYNC_LITERATURE,
        TaskType.REFRESH_SITUATION_SOURCES,
        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
        TaskType.PROCESS_DATA,
    ],
)
def test_each_formal_store_prevents_release_from_claiming(store_type):
    assert store_type in COLLECTION_STORE_TASK_TYPES
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types([store_type])


def test_release_blocks_every_collection_store_until_projection_commit():
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )
    assert COLLECTION_STORE_TASK_TYPES <= _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )


def test_waiting_release_drains_existing_writers_without_claiming_new_ones():
    blocked = _release_memory_blocked_task_types([], release_waiting=True)
    assert COLLECTION_STORE_TASK_TYPES <= blocked
    assert TaskType.EXPORT_DATA not in blocked
    assert TaskType.SEND_EMAIL not in blocked
    # Existing writes finish before EXPORT_DATA becomes runnable.
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.CRAWL_DATA], release_waiting=True
    )


def test_store_exclusion_does_not_change_other_task_concurrency():
    assert _release_memory_blocked_task_types([TaskType.SEND_EMAIL]) == set()
    # Knowledge brief upserts still rewrite rows the site reads up front.
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.UPDATE_DISEASE_KNOWLEDGE]
    )


def test_ai_review_parents_leave_the_release_slot_open():
    assert TaskType.EXPORT_DATA not in _release_memory_blocked_task_types(
        [TaskType.AGENT_WORKFLOW]
    )
    assert TaskType.EXPORT_DATA not in _release_memory_blocked_task_types(
        [TaskType.ENRICH_LITERATURE]
    )
    blocked = _release_memory_blocked_task_types([], release_waiting=True)
    assert TaskType.AGENT_WORKFLOW not in blocked
    assert TaskType.ENRICH_LITERATURE not in blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("active_type,active_reports,queued_type,queued_reports,lane_blocked,expected", [
    (TaskType.CRAWL_DATA, False, TaskType.EXPORT_DATA, True, set(), True),
    (TaskType.CRAWL_DATA, False, TaskType.EXPORT_DATA, False, set(), False),
    (TaskType.EXPORT_DATA, True, TaskType.CRAWL_DATA, False, set(), True),
    (TaskType.EXPORT_DATA, True, TaskType.EXPORT_DATA, True, {TaskType.EXPORT_DATA}, False),
    (TaskType.EXPORT_DATA, False, TaskType.CRAWL_DATA, False, set(), False),
])
async def test_report_only_lane_keeps_full_release_exclusion(
    tmp_path, monkeypatch, active_type, active_reports, queued_type, queued_reports, lane_blocked, expected
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Task.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def database():
        async with maker() as session:
            yield session
    monkeypatch.setattr(task_worker, "get_database", database)
    async with maker() as session:
        session.add_all([
            Task(task_uuid="active", task_name="active", task_type=active_type,
                 status=TaskStatus.RUNNING, input_data={"reports_only": active_reports}),
            Task(task_uuid="queued", task_name="queued", task_type=queued_type,
                 status=TaskStatus.QUEUED, input_data={"reports_only": queued_reports}),
        ])
        await session.commit()
    claimed = await task_worker._claim_next_task_uuid("worker", blocked_task_types=lane_blocked)
    assert (claimed is not None) is expected
    if expected:
        assert claimed == ("queued", queued_type)
    await engine.dispose()
