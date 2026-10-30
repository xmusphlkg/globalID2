"""Collection stage recording: attempt semantics and fused-write failure tolerance."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

pytest.importorskip("aiosqlite")

from src.core import database as database_module  # noqa: E402
from src.domain import Base, RunStage  # noqa: E402
from src.services.ingestion import stages as stages_module  # noqa: E402

TABLES = [RunStage.__table__]

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


async def _rows(db, task_uuid: str) -> list[RunStage]:
    async with db() as session:
        return list(
            (
                await session.execute(
                    select(RunStage)
                    .where(RunStage.task_uuid == task_uuid)
                    .order_by(RunStage.attempt, RunStage.stage)
                )
            ).scalars().all()
        )


async def test_retry_produces_new_attempt_and_keeps_old_rows(db):
    assert await stages_module.record_fused_outcome(task_uuid="t1", started_at=STARTED, outcome="completed")
    assert await stages_module.record_fused_outcome(task_uuid="t1", started_at=STARTED, outcome="completed")

    rows = await _rows(db, "t1")
    assert {r.attempt for r in rows} == {1, 2}

    first = [r for r in rows if r.attempt == 1]
    assert {(r.stage, r.status) for r in first} == {
        ("collect", "completed"),
        ("organize", "skipped"),
        ("store", "skipped"),
    }
    assert all(r.metrics.get("fused") for r in first if r.stage == "collect")


class _BrokenDB:
    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, *exc):
        return False


async def test_fused_write_failure_does_not_raise(db, monkeypatch):
    monkeypatch.setattr(stages_module, "get_db", lambda: _BrokenDB())

    result = await stages_module.record_fused_outcome(task_uuid="t2", started_at=STARTED, outcome="completed")
    assert result is False
    assert await _rows(db, "t2") == []
