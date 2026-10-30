"""publish_dirty: only successful stores mark a scope dirty, and the pending query
used by the release side is exactly ``published_at IS NULL``."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

pytest.importorskip("aiosqlite")

from src.core import database as database_module  # noqa: E402
from src.domain import Base, PublishDirty, RunStage  # noqa: E402
from src.services.ingestion import stages as stages_module  # noqa: E402

TABLES = [RunStage.__table__, PublishDirty.__table__]

STARTED = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "_engine", engine)
    monkeypatch.setattr(database_module, "_session_maker", maker)
    yield maker
    await engine.dispose()


async def _pending(db) -> list[PublishDirty]:
    async with db() as session:
        return list(
            (
                await session.execute(select(PublishDirty).where(PublishDirty.published_at.is_(None)))
            ).scalars().all()
        )


async def _all_dirty(db) -> list[PublishDirty]:
    async with db() as session:
        return list((await session.execute(select(PublishDirty))).scalars().all())


async def test_failed_store_leaves_no_dirty_row(db):
    assert await stages_module.record_fused_outcome(
        task_uuid="t1", started_at=STARTED, outcome="failed", scope_key="US"
    )
    assert await _all_dirty(db) == []


async def test_successful_store_marks_scope_dirty(db):
    assert await stages_module.record_fused_outcome(
        task_uuid="t2", started_at=STARTED, outcome="completed", scope_key="US"
    )

    dirty = await _all_dirty(db)
    assert len(dirty) == 1
    assert dirty[0].line == "surveillance"
    assert dirty[0].scope_key == "US"
    assert dirty[0].source_task_uuid == "t2"
    assert dirty[0].published_task_uuid is None
    assert dirty[0].published_at is None
    pending = await _pending(db)
    assert len(pending) == 1
    assert (pending[0].line, pending[0].scope_key, pending[0].source_task_uuid) == ("surveillance", "US", "t2")


async def test_store_rollback_leaves_no_dirty_row(db, monkeypatch):
    original = stages_module.record_stage

    async def failing_store(db, *, stage, **kwargs):
        if stage == "store":
            raise RuntimeError("store exploded")
        return await original(db, stage=stage, **kwargs)

    monkeypatch.setattr(stages_module, "record_stage", failing_store)

    assert (
        await stages_module.record_fused_outcome(
            task_uuid="t3", started_at=STARTED, outcome="completed", scope_key="US"
        )
        is False
    )

    async with db() as session:
        assert (await session.execute(select(RunStage))).scalars().all() == []
    assert await _all_dirty(db) == []


async def test_published_dirty_row_is_not_seen_again(db):
    await stages_module.record_fused_outcome(task_uuid="t4", started_at=STARTED, outcome="completed", scope_key="US")
    assert len(await _pending(db)) == 1

    # Release side writes back only after production release is verified.
    async with db() as session:
        dirty = (await session.execute(select(PublishDirty))).scalar_one()
        dirty.published_task_uuid = "release-1"
        dirty.published_at = datetime(2026, 10, 30, 13, 0, 0, tzinfo=timezone.utc)
        await session.commit()

    assert await _pending(db) == []
