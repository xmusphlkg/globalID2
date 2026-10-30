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

from src.core import database as database_module
from src.domain import Base, Country, PublishDirty, RunStage
from src.services.ingestion import stages as stages_module
from tests.unit.publication_sqlite import create_publication_tables

TABLES = [Country.__table__, RunStage.__table__, PublishDirty.__table__]

STARTED = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
        await conn.run_sync(create_publication_tables)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "_engine", engine)
    monkeypatch.setattr(database_module, "_session_maker", maker)
    yield maker
    await engine.dispose()


async def _pending(db) -> list[PublishDirty]:
    async with db() as session:
        return list(
            (
                await session.execute(
                    select(PublishDirty).where(PublishDirty.published_at.is_(None))
                )
            )
            .scalars()
            .all()
        )


async def _all_dirty(db) -> list[PublishDirty]:
    async with db() as session:
        return list((await session.execute(select(PublishDirty))).scalars().all())


async def test_successful_store_marks_scope_dirty(db):
    async with database_module.get_db() as session:
        await stages_module.mark_dirty(
            session, task_uuid="t2", line="surveillance", scope_key="US"
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
    assert (pending[0].line, pending[0].scope_key, pending[0].source_task_uuid) == (
        "surveillance",
        "US",
        "t2",
    )


async def test_published_dirty_row_is_not_seen_again(db):
    async with database_module.get_db() as session:
        await stages_module.mark_dirty(
            session, task_uuid="t4", line="surveillance", scope_key="US"
        )
    assert len(await _pending(db)) == 1

    # Release side writes back only after production release is verified.
    async with db() as session:
        dirty = (await session.execute(select(PublishDirty))).scalar_one()
        dirty.published_task_uuid = "release-1"
        dirty.published_at = datetime(2026, 10, 30, 13, 0, 0, tzinfo=timezone.utc)
        await session.commit()

    assert await _pending(db) == []


async def test_store_transaction_rollback_also_discards_dirty(db):
    with pytest.raises(RuntimeError, match="formal rows failed"):
        async with database_module.get_db() as session:
            await stages_module.mark_dirty(
                session, line="literature", scope_key="*", task_uuid="rollback"
            )
            await session.flush()
            raise RuntimeError("formal rows failed")
    assert await _pending(db) == []


async def test_release_acknowledges_only_selected_ids_and_is_atomic(db, monkeypatch):
    from src.services.data_release import projection

    async with database_module.get_db() as session:
        await stages_module.mark_dirty(
            session, line="surveillance", scope_key="US", task_uuid="collection-1"
        )
        await stages_module.mark_dirty(
            session, line="surveillance", scope_key="JP", task_uuid="collection-2"
        )
    selected = (await _pending(db))[0].id
    calls = []

    async def context():
        return {"countries_by_code": {"US": {}, "JP": {}}}

    async def publish(session, code, context, *, force):
        calls.append((code, force))
        if len(calls) == 1:
            raise RuntimeError("projection failed")

    monkeypatch.setattr(projection, "_load_publish_context", context)
    monkeypatch.setattr(projection, "_publish_country", publish)
    with pytest.raises(RuntimeError, match="projection failed"):
        async with database_module.get_db() as session:
            await projection.commit_release_projection(
                session,
                task_uuid="release-1",
                country_codes=["US"],
                full=False,
                dirty_ids=[selected],
            )
    assert len(await _pending(db)) == 2
    async with database_module.get_db() as session:
        await projection.commit_release_projection(
            session,
            task_uuid="release-2",
            country_codes=["US"],
            full=False,
            dirty_ids=[selected],
        )
    pending = await _pending(db)
    assert [row.scope_key for row in pending] == ["JP"]
    assert calls[:2] == [("US", True), ("US", True)]
    assert all(code == "US" or code.startswith("US-") for code, _ in calls)
