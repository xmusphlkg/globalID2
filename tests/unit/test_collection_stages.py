"""Collection stage recording preserves attempts and propagates storage failures."""

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


class _BrokenDB:
    async def __aenter__(self):
        raise RuntimeError("db down")

    async def __aexit__(self, *exc):
        return False


async def test_stage_write_failure_is_fatal(db, monkeypatch):
    monkeypatch.setattr(stages_module, "get_db", lambda: _BrokenDB())

    with pytest.raises(RuntimeError, match="db down"):
        await stages_module.StageRun.start("t2", "surveillance")
    assert await _rows(db, "t2") == []


async def test_three_stage_terminal_updates_and_retry_preserve_history(db):
    for expected_attempt in (1, 2):
        run = await stages_module.StageRun.start("three-stage", "literature")
        assert run.attempt == expected_attempt
        for stage in ("collect", "organize", "store"):
            await run.transition(stage)
        await run.finish(metrics={"rows": 4})
    rows = await _rows(db, "three-stage")
    assert len(rows) == 6
    assert all(row.status == "completed" for row in rows)
    assert all(not row.metrics.get("fused") for row in rows)


async def test_three_stage_failure_keeps_unreached_stages_visible(db):
    run = await stages_module.StageRun.start("failed-run", "situation")
    await run.transition("collect")
    await run.transition("organize")
    await run.finish(error="extraction failed")
    rows = await _rows(db, "failed-run")
    assert {row.stage: row.status for row in rows} == {"collect": "completed", "organize": "failed", "store": "skipped"}
