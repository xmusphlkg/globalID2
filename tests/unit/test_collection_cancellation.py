"""Interrupted collection must leave a failed stage, including empty errors."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.domain import Base, Task, TaskType, TaskStatus
from src.domain.collection import RunStage, PublishDirty
from src.services.ingestion import stages


@pytest.mark.parametrize("line", ["literature", "situation"])
async def test_cancelled_collection_leaves_failed_stage_without_dirty(monkeypatch, line):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, RunStage.__table__, PublishDirty.__table__]
        ))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def database():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    monkeypatch.setattr(stages, "get_db", database)
    identity = str(uuid4())
    async with database() as db:
        task = Task(task_uuid=identity, task_name="Cancellation fixture", task_type=TaskType.SYNC_LITERATURE if line == "literature" else TaskType.REFRESH_SITUATION_SOURCES, status=TaskStatus.RUNNING, input_data={})
        db.add(task)
    if line == "literature":
        from src.literature.pipeline import LiteraturePipeline
        pipeline = LiteraturePipeline(SimpleNamespace())
        monkeypatch.setattr(pipeline, "_resolve_start", AsyncMock(return_value=(datetime.now(timezone.utc), None)))
        monkeypatch.setattr(pipeline, "_create_run", AsyncMock())
        monkeypatch.setattr(pipeline, "_supersede_earlier_running_runs", AsyncMock(side_effect=asyncio.CancelledError()))
        finish = AsyncMock()
        monkeypatch.setattr(pipeline, "_finish_run", finish)
        run = lambda: pipeline.execute(task)
    else:
        from src.services.situation_notices import runner
        def interrupted(**kwargs):
            raise asyncio.CancelledError()
        monkeypatch.setattr(runner, "collect", interrupted)
        run = lambda: runner.run_situation(task_uuid=identity, skip_ai=True)
    try:
        with pytest.raises(asyncio.CancelledError):
            await run()
        async with database() as db:
            rows = list((await db.execute(select(RunStage))).scalars())
            assert {row.stage: row.status for row in rows} == {
                "collect": "failed", "organize": "skipped", "store": "skipped"
            }
            assert next(row for row in rows if row.stage == "collect").error == "CancelledError"
            assert list((await db.execute(select(PublishDirty))).scalars()) == []
        if line == "literature":
            assert finish.await_args.args[1] == "failed"
    finally:
        await engine.dispose()
