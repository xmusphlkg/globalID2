"""Opt-in PostgreSQL check of concurrent enqueue and its separate FK insert."""

import asyncio
from contextlib import asynccontextmanager
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import get_config
from src.domain import Country, Task, TaskStatus
from src.services import crawl_task_service as module


@pytest.mark.skipif(
    os.getenv("GLOBALID_TEST_PG_ENQUEUE") != "1",
    reason="explicit isolated-schema PostgreSQL concurrency check",
)
async def test_two_simultaneous_enqueues_create_one_task(monkeypatch):
    schema = "enqueue_test_" + uuid4().hex
    admin = create_async_engine(get_config().database.url)
    engine = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            get_config().database.url,
            connect_args={"server_settings": {"search_path": schema}},
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE reports (id INTEGER PRIMARY KEY)"))
            await conn.run_sync(Country.__table__.create)
            await conn.run_sync(Task.__table__.create)
        async with sessions.begin() as db:
            db.add(Country(code="US", name="US", name_en="US"))

        @asynccontextmanager
        async def database():
            async with sessions.begin() as db:
                yield db

        async def create_task(**values):
            # Exercise the real FK insert on a second connection while the
            # enqueue transaction holds the country lock.
            async with sessions.begin() as db:
                task = Task(**values, status=TaskStatus.PENDING)
                db.add(task)
                await db.flush()
                await asyncio.sleep(0.05)
                return task

        async def update_status(uid, status):
            async with sessions.begin() as db:
                task = (await db.execute(select(Task).where(Task.task_uuid == uid))).scalar_one()
                task.status = status
                return task

        monkeypatch.setattr(module, "get_database", database)
        monkeypatch.setattr(module.task_manager, "create_task", create_task)
        monkeypatch.setattr(module.task_manager, "update_task_status", update_status)
        service = module.CrawlTaskService()
        results = await asyncio.wait_for(asyncio.gather(
            service.enqueue_crawl_task(country_code="US"),
            service.enqueue_crawl_task(country_code="US"),
        ), timeout=15)
        assert sorted(result.created for result in results) == [False, True]
        assert results[0].task.task_uuid == results[1].task.task_uuid
        async with sessions() as db:
            tasks = (await db.execute(select(Task))).scalars().all()
            assert len(tasks) == 1
            assert tasks[0].status == TaskStatus.QUEUED
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
