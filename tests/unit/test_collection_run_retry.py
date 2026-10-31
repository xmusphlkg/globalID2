"""Operator retry reuses completed collection UUIDs without retry debt."""
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.domain import Base, Country, Task, TaskType, TaskStatus, TaskPriority
from src.control_plane import operations


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Country.__table__, Task.__table__]))
    monkeypatch.setattr(operations.control_plane_events, 'publish_task_event', AsyncMock())
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.parametrize('kind', [TaskType.CRAWL_DATA,TaskType.SYNC_LITERATURE,TaskType.REFRESH_SITUATION_SOURCES,TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,TaskType.UPDATE_DISEASE_KNOWLEDGE])
async def test_completed_collection_can_retry_with_same_uuid_and_retry_count(db, kind):
    task = Task(task_uuid=str(uuid4()),task_type=kind,task_name='collection',status=TaskStatus.COMPLETED,priority=TaskPriority.NORMAL,retry_count=2,input_data={})
    db.add(task)
    await db.commit()
    original = task.task_uuid
    result = await operations.TaskOperationsService(db).retry(original)
    assert result['task_uuid'] == original
    assert task.task_uuid == original
    assert task.status == TaskStatus.QUEUED
    assert task.retry_count == 2


async def test_completed_release_cannot_retry_as_collection(db):
    task = Task(task_uuid=str(uuid4()),task_type=TaskType.EXPORT_DATA,task_name='release',status=TaskStatus.COMPLETED,priority=TaskPriority.NORMAL,input_data={})
    db.add(task)
    await db.commit()
    with pytest.raises(RuntimeError,match='cannot be retried'):
        await operations.TaskOperationsService(db).retry(task.task_uuid)


async def test_retry_cannot_bypass_country_active_task_conflict(db):
    original = Task(task_uuid=str(uuid4()),task_type=TaskType.CRAWL_DATA,task_name='done',country_id=1,status=TaskStatus.COMPLETED,priority=TaskPriority.NORMAL,input_data={})
    active = Task(task_uuid=str(uuid4()),task_type=TaskType.CRAWL_DATA,task_name='active',country_id=1,status=TaskStatus.QUEUED,priority=TaskPriority.NORMAL,input_data={})
    db.add_all([original,active])
    await db.commit()
    with pytest.raises(RuntimeError,match='already has'):
        await operations.TaskOperationsService(db).retry(original.task_uuid)
    assert original.status == TaskStatus.COMPLETED
