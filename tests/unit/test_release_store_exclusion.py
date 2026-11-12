"""Read snapshots let release and collection lanes run independently."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.release_admission import ReleaseAdmission
from src.domain import Base, Task, TaskPriority, TaskStatus, TaskType
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
def test_each_formal_store_allows_snapshot_release_claim(store_type):
    assert store_type in COLLECTION_STORE_TASK_TYPES
    assert TaskType.EXPORT_DATA not in _release_memory_blocked_task_types([store_type])


def test_release_serializes_only_other_release_tasks():
    assert TaskType.EXPORT_DATA in _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )
    assert not COLLECTION_STORE_TASK_TYPES & _release_memory_blocked_task_types(
        [TaskType.EXPORT_DATA]
    )


def test_waiting_release_does_not_pause_collection_claims():
    blocked = _release_memory_blocked_task_types([], release_waiting=True)
    assert not COLLECTION_STORE_TASK_TYPES & blocked
    assert TaskType.EXPORT_DATA not in blocked
    assert TaskType.SEND_EMAIL not in blocked
    # PostgreSQL snapshot isolation permits active formal writes.
    assert TaskType.EXPORT_DATA not in _release_memory_blocked_task_types(
        [TaskType.CRAWL_DATA], release_waiting=True
    )


def test_store_exclusion_does_not_change_other_task_concurrency():
    assert _release_memory_blocked_task_types([TaskType.SEND_EMAIL]) == set()
    # Knowledge brief upserts still rewrite rows the site reads up front.
    assert TaskType.EXPORT_DATA not in _release_memory_blocked_task_types(
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
    (TaskType.CRAWL_DATA, False, TaskType.EXPORT_DATA, False, set(), True),
    (TaskType.EXPORT_DATA, True, TaskType.CRAWL_DATA, False, set(), True),
    (TaskType.EXPORT_DATA, True, TaskType.EXPORT_DATA, True, {TaskType.EXPORT_DATA}, False),
    (TaskType.EXPORT_DATA, False, TaskType.CRAWL_DATA, False, set(), True),
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


@pytest.fixture
async def worker_database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Task.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as session:
            yield session

    monkeypatch.setattr(task_worker, "get_database", database)
    monkeypatch.setattr(task_worker, "release_admission", ReleaseAdmission())
    monkeypatch.setattr(task_worker, "_claimed_raw_archive_uuids", set())
    monkeypatch.setattr(task_worker, "_claimed_data_release_uuids", set())
    yield maker
    await engine.dispose()


@pytest.mark.parametrize('active_flags,queued_flags,expected', [
    ({'data_only': True, 'data_line': 'literature'}, {'data_only': True, 'data_line': 'surveillance'}, True),
    ({'data_only': True, 'data_line': 'surveillance'}, {'data_only': True, 'data_line': 'literature'}, True),
    ({'data_only': True, 'data_line': 'situation'}, {'data_only': True, 'data_line': 'literature'}, True),
    ({'data_only': True, 'data_line': 'literature'}, {'data_only': True, 'data_line': 'literature'}, False),
    ({'content_only': True}, {'data_only': True, 'data_line': 'literature'}, False),
    ({'data_only': True, 'data_line': 'literature'}, {'content_only': True}, False),
])
async def test_literature_lane_is_independent_of_json_but_excludes_static_builds(worker_database, active_flags, queued_flags, expected):
    async with worker_database() as db:
        db.add_all([Task(task_uuid='active-literature-test', task_name='active', task_type=TaskType.EXPORT_DATA,
                         status=TaskStatus.RUNNING, input_data=active_flags),
                    Task(task_uuid='queued-literature-test', task_name='queued', task_type=TaskType.EXPORT_DATA,
                         status=TaskStatus.QUEUED, input_data=queued_flags)])
        await db.commit()
    claimed = await task_worker._claim_next_task_uuid('worker')
    assert (claimed is not None) is expected


@pytest.mark.parametrize("active_status", [TaskStatus.RUNNING, TaskStatus.RETRYING])
@pytest.mark.parametrize("active_flags,queued_flags,blocked,expected", [
    ({"content_only": True}, {"data_only": True}, {TaskType.EXPORT_DATA}, True),
    ({"data_only": True}, {"content_only": True}, set(), True),
    ({"data_only": True}, {"data_only": True}, set(), False),
    ({"raw_archive_only": True}, {"data_only": True}, set(), True),
    ({"content_only": True}, {"data_only": True}, set(TaskType), True),
    ({"content_only": True, "situation_detached": True}, {"data_only": True, "data_line": "situation"}, set(TaskType), True),
    ({"content_only": True}, {"data_only": True, "data_line": "situation"}, set(), False),
    ({"data_only": True, "data_line": "situation"}, {"content_only": True}, set(), False),
    ({"data_only": True, "data_line": "situation"}, {"content_only": True, "situation_detached": True}, set(), True),
    ({"data_only": True}, {"data_only": True, "data_line": "situation"}, set(), False),
    ({"data_only": True, "data_line": "situation"}, {"data_only": True}, set(), False),
])
async def test_json_has_independent_single_slot_and_bypasses_full_regular_lanes(
    worker_database, active_flags, queued_flags, blocked, expected, active_status,
):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="active", task_type=TaskType.EXPORT_DATA, task_name="Active",
                 status=active_status, input_data=active_flags),
            Task(task_uuid="json-or-site", task_type=TaskType.EXPORT_DATA, task_name="Queued",
                 status=TaskStatus.QUEUED, input_data=queued_flags),
        ])
        await session.commit()
    claimed = await task_worker._claim_next_task_uuid("worker", blocked_task_types=blocked)
    assert (claimed is not None) is expected
    if expected and queued_flags.get("data_only"):
        assert "json-or-site" in task_worker._claimed_data_release_uuids


async def test_parked_json_retry_does_not_block_site_or_collection(worker_database):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="retrying", task_type=TaskType.EXPORT_DATA, task_name="JSON retry",
                 status=TaskStatus.RETRYING, input_data={"data_only": True}),
            Task(task_uuid="crawl", task_type=TaskType.CRAWL_DATA, task_name="Crawl", status=TaskStatus.QUEUED),
            Task(task_uuid="site", task_type=TaskType.EXPORT_DATA, task_name="Site", status=TaskStatus.QUEUED,
                 input_data={"content_only": True}),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("worker") == ("site", TaskType.EXPORT_DATA)
    assert await task_worker._claim_next_task_uuid("worker") == ("crawl", TaskType.CRAWL_DATA)


@pytest.mark.parametrize("active_data,active_status", [
    (False, TaskStatus.RUNNING), (False, TaskStatus.RETRYING),
    (True, TaskStatus.RUNNING), (True, TaskStatus.RETRYING),
])
async def test_legacy_site_and_json_cannot_overlap_in_either_direction(worker_database, active_data, active_status):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="active", task_type=TaskType.EXPORT_DATA, task_name="Active",
                 status=active_status, input_data={"data_only": True} if active_data else {}),
            Task(task_uuid="incompatible", task_type=TaskType.EXPORT_DATA, task_name="Queued",
                 status=TaskStatus.QUEUED, input_data={} if active_data else {"data_only": True}),
            Task(task_uuid="crawl", task_type=TaskType.CRAWL_DATA, task_name="Collection", status=TaskStatus.QUEUED),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("worker") == ("crawl", TaskType.CRAWL_DATA)
    assert await task_worker._claim_next_task_uuid("worker") is None


@pytest.mark.parametrize("first_is_data", [False, True])
async def test_queued_legacy_and_json_reserve_both_channels_when_first_claimed(worker_database, first_is_data):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="first", task_type=TaskType.EXPORT_DATA, task_name="First",
                 status=TaskStatus.QUEUED, created_at=now,
                 input_data={"data_only": True} if first_is_data else {}),
            Task(task_uuid="second", task_type=TaskType.EXPORT_DATA, task_name="Second",
                 status=TaskStatus.QUEUED, created_at=now + timedelta(seconds=1),
                 input_data={} if first_is_data else {"data_only": True}),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("worker") == ("first", TaskType.EXPORT_DATA)
    assert await task_worker._claim_next_task_uuid("worker") is None


@pytest.mark.parametrize("active_archive,queued_archive,queued_type,expected", [
    (True, False, TaskType.EXPORT_DATA, True),
    (True, False, TaskType.CRAWL_DATA, True),
    (False, True, TaskType.EXPORT_DATA, True),
    (True, True, TaskType.EXPORT_DATA, False),
])
async def test_archive_has_independent_serial_lane(
    worker_database, active_archive, queued_archive, queued_type, expected,
):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="active", task_name="active", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.RUNNING, input_data={"raw_archive_only": active_archive}),
            Task(task_uuid="next", task_name="next", task_type=queued_type,
                 status=TaskStatus.QUEUED, input_data={"raw_archive_only": queued_archive}),
        ])
        await session.commit()
    result = await task_worker._claim_next_task_uuid("worker")
    assert (result is not None) is expected
    if result and queued_archive:
        assert "next" in task_worker._claimed_raw_archive_uuids


async def test_archive_queue_does_not_steal_release_priority_or_pause_collection(worker_database):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="archive", task_name="Archive", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.QUEUED, priority=TaskPriority.URGENT,
                 input_data={"raw_archive_only": True}),
            Task(task_uuid="crawl", task_name="Crawl", task_type=TaskType.CRAWL_DATA,
                 status=TaskStatus.QUEUED, priority=TaskPriority.NORMAL),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("worker") == ("crawl", TaskType.CRAWL_DATA)


async def test_waiting_archive_retry_does_not_deadlock_queued_successor(worker_database):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="retry", task_name="Retry", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.RETRYING, input_data={"raw_archive_only": True}),
            Task(task_uuid="next", task_name="Next archive", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.QUEUED, input_data={"raw_archive_only": True}),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("worker") == ("next", TaskType.EXPORT_DATA)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_type", COLLECTION_STORE_TASK_TYPES)
async def test_release_claim_preempts_cooperative_writer_with_full_general_lane(worker_database, store_type):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="writer", task_name="writer", task_type=store_type, status=TaskStatus.RUNNING),
            Task(task_uuid="release", task_name="release", task_type=TaskType.EXPORT_DATA, status=TaskStatus.QUEUED),
        ])
        await session.commit()
    with task_worker.release_admission.writer("writer"):
        assert await task_worker._claim_next_task_uuid(
            "owner", blocked_task_types=set(task_worker.GENERAL_TASK_TYPES)
        ) == ("release", TaskType.EXPORT_DATA)


@pytest.mark.asyncio
async def test_release_outranks_older_urgent_tasks_even_with_legacy_low_priority(worker_database):
    from datetime import datetime, timezone

    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="urgent", task_name="urgent", task_type=TaskType.SEND_EMAIL,
                 status=TaskStatus.QUEUED, priority=TaskPriority.URGENT,
                 created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            Task(task_uuid="release", task_name="release", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.QUEUED, priority=TaskPriority.LOW),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("owner") == ("release", TaskType.EXPORT_DATA)


@pytest.mark.asyncio
async def test_waiting_release_without_reports_flag_allows_new_collection_claims(worker_database):
    async with worker_database() as session:
        session.add_all([
            Task(task_uuid="writer", task_name="writer", task_type=TaskType.CRAWL_DATA, status=TaskStatus.QUEUED),
            Task(task_uuid="release", task_name="release", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.QUEUED, input_data={}),
        ])
        await session.commit()
    assert await task_worker._claim_next_task_uuid("owner", blocked_task_types={TaskType.EXPORT_DATA}) == ("writer", TaskType.CRAWL_DATA)
