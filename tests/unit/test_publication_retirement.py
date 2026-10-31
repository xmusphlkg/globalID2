"""Disabled public snapshots retire only with the successful release transaction."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, insert, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Country, DiseaseSeriesObservation, PublishDirty
from src.services.data_release import projection
from tests.unit.publication_sqlite import (
    PUBLIC_SERIES,
    PUBLISH_STATE,
    create_publication_tables,
)

NOW = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'retirement.sqlite'}"
    )

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_functions(connection, _record):
        connection.create_function(
            "btrim",
            1,
            lambda value: value.strip() if value else value,
            deterministic=True,
        )

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Country.__table__,
                    PublishDirty.__table__,
                    DiseaseSeriesObservation.__table__,
                ],
            )
        )
        await connection.run_sync(create_publication_tables)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                Country(code="IE", name="Ireland", name_en="Ireland", is_active=True),
                Country(code="US", name="US", name_en="US", is_active=False),
                Country(code="JP", name="Japan", name_en="Japan", is_active=True),
            ]
        )
        session.add(
            DiseaseSeriesObservation(
                time=NOW,
                series_code="ie:test",
                geography_key="country:IE:national",
                dimension_key="all",
                value=17,
                unit="cases",
                quality_status="raw",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            PublishDirty(
                line="surveillance",
                scope_key="IE",
                source_task_uuid="ie-collect",
                marked_at=NOW,
            )
        )
        for code in ("IE", "US", "JP"):
            await session.execute(
                insert(PUBLIC_SERIES).values(
                    country_code=code,
                    disease_id="D001",
                    period_starts=["2026-10-01"],
                    cases=[17],
                    deaths=[None],
                    incidence=[None],
                    granularity="monthly",
                    data_layer="series_registry",
                    source_series_code=f"{code}:test",
                    point_count=1,
                    total_cases=17,
                    total_deaths=0,
                    content_hash="a" * 64,
                    source_high_water=NOW,
                    projection_version="public-series-v1",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.execute(
                insert(PUBLISH_STATE).values(
                    country_code=code,
                    series_count=1,
                    point_count=1,
                    content_hash="b" * 64,
                    source_high_water=NOW,
                    projection_version="public-series-v1",
                    published_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.commit()
    forbidden = AsyncMock(
        side_effect=AssertionError("empty-country release must not project curves")
    )
    monkeypatch.setattr(projection, "_load_publish_context", forbidden)
    monkeypatch.setattr(projection, "_publish_country", forbidden)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)

    yield maker, statements, forbidden
    await engine.dispose()


async def snapshot(maker):
    async with maker() as session:
        return {
            name: [
                tuple(row)
                for row in (
                    await session.execute(text(f"SELECT * FROM {name} ORDER BY 1"))
                ).all()
            ]
            for name in (
                "public_disease_series",
                "country_publish_state",
                "disease_series_observations",
            )
        }


async def finalize(session):
    marker = (await session.execute(select(PublishDirty))).scalar_one()
    await projection.commit_release_projection(
        session,
        task_uuid="verified-release",
        country_codes=[],
        full=False,
        dirty_ids=[marker.id],
    )


async def test_successful_commit_removes_disabled_snapshots_and_preserves_facts(db):
    maker, statements, forbidden = db
    before = await snapshot(maker)
    statements.clear()
    async with maker() as session:
        await finalize(session)
        await session.commit()
    assert not any(
        "disease_series_observations" in statement for statement in statements
    )
    forbidden.assert_not_awaited()
    after = await snapshot(maker)
    for name in ("public_disease_series", "country_publish_state"):
        assert after[name] == [row for row in before[name] if row[0] == "JP"]
    assert after["disease_series_observations"] == before["disease_series_observations"]
    async with maker() as session:
        marker = (await session.execute(select(PublishDirty))).scalar_one()
        assert (
            marker.scope_key == "IE"
            and marker.published_at is None
            and marker.published_task_uuid is None
        )
    statements.clear()
    async with maker() as session:
        await finalize(session)
        await session.commit()
    assert not any(
        statement.startswith(("DELETE", "UPDATE"))
        for statement in statements
        if "publish_dirty" not in statement
    )
    assert await snapshot(maker) == after


async def test_failed_final_transaction_restores_public_snapshots(db):
    maker, _, forbidden = db
    before = await snapshot(maker)
    with pytest.raises(RuntimeError, match="final commit failed"):
        async with maker() as session:
            try:
                await finalize(session)
                raise RuntimeError("final commit failed")
            except BaseException:
                await session.rollback()
                raise
    assert await snapshot(maker) == before
    forbidden.assert_not_awaited()
    async with maker() as session:
        assert (
            await session.execute(select(PublishDirty))
        ).scalar_one().published_at is None


@pytest.mark.parametrize(
    "missing_side", ["public_disease_series", "country_publish_state"]
)
async def test_cleanup_also_finds_single_sided_retired_snapshots(db, missing_side):
    maker, _, _ = db
    async with maker() as session:
        await session.execute(
            text(f"DELETE FROM {missing_side} WHERE country_code='IE'")
        )
        await session.commit()
    async with maker() as session:
        await finalize(session)
        await session.commit()
    after = await snapshot(maker)
    assert all(
        row[0] == "JP"
        for name in ("public_disease_series", "country_publish_state")
        for row in after[name]
    )


async def test_missing_publication_migrations_fail_instead_of_creating_tables(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'unmigrated.sqlite'}"
    )
    maker = async_sessionmaker(engine)
    try:
        with pytest.raises(OperationalError, match="no such table"):
            async with maker() as session:
                await projection.commit_release_projection(
                    session,
                    task_uuid="release",
                    country_codes=[],
                    full=False,
                    dirty_ids=[],
                )
    finally:
        await engine.dispose()
