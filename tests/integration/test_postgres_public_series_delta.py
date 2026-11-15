"""Exercise differential public projection writes in a disposable PostgreSQL schema."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.public_series import CountryPublishState, PublicDiseaseSeries
from src.generation.public_series_store import _replace_country_rows, build_public_series_row, country_publish_hash


@pytest.fixture
async def projection_db():
    url = os.environ.get("TEST_SNAPSHOT_DATABASE_URL")
    if not url:
        pytest.skip("TEST_SNAPSHOT_DATABASE_URL is required")
    schema = "test_public_delta_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE countries (code varchar(10) PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE standard_diseases (disease_id varchar(100) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO countries VALUES ('CA'), ('US')"))
            await conn.execute(text("INSERT INTO standard_diseases VALUES ('D001'), ('D002'), ('D003')"))
            await conn.run_sync(lambda sync: PublicDiseaseSeries.__table__.create(sync))
            await conn.run_sync(lambda sync: CountryPublishState.__table__.create(sync))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


def curve(disease, value=1, *, country="CA", water=None):
    return build_public_series_row(country, disease, {
        "dates": ["2026-01-01"], "cases": [value], "deaths": [None],
        "incidence_rates": [None], "period_granularity": "weekly",
    }, source_high_water=water)


async def apply(maker, rows, *, water=None):
    async with maker.begin() as db:
        return await _replace_country_rows(db, "CA", rows, source_high_water=water,
                                          stored_disease_ids={"D001", "D002", "D003"})


async def snapshot(maker):
    async with maker() as db:
        curves = (await db.execute(text("SELECT *, xmin::text AS revision FROM public_disease_series ORDER BY disease_id"))).mappings().all()
        state = (await db.execute(text("SELECT *, xmin::text AS revision FROM country_publish_state"))).mappings().all()
        return [dict(row) for row in curves], [dict(row) for row in state]


async def test_noop_watermark_change_delete_insert_and_empty_country(projection_db):
    maker = projection_db
    water = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [curve("D001"), curve("D002")]
    counts = await apply(maker, rows, water=water)
    assert counts == {"inserted": 2, "changed": 0, "deleted": 0, "watermark_only": 0, "unchanged": 0}
    original = await snapshot(maker)
    counts = await apply(maker, rows, water=water)
    assert counts["unchanged"] == 2
    assert await snapshot(maker) == original  # includes xmin: no hidden rewrites
    assert "created_at" not in rows[0]  # caller's frozen candidate stays immutable

    counts = await apply(maker, rows, water=water + timedelta(days=1))
    assert counts["watermark_only"] == 2 and counts["changed"] == 0
    after_water = await snapshot(maker)
    assert after_water[0][0]["cases"] == original[0][0]["cases"]
    assert after_water[0][0]["created_at"] == original[0][0]["created_at"]

    replacement = [curve("D001", 4), curve("D003", 7)]
    counts = await apply(maker, replacement, water=water + timedelta(days=2))
    assert counts == {"inserted": 1, "changed": 1, "deleted": 1, "watermark_only": 0, "unchanged": 0}
    curves, state = await snapshot(maker)
    assert [row["disease_id"] for row in curves] == ["D001", "D003"]
    assert curves[0]["cases"] == [4]
    assert curves[0]["created_at"] == original[0][0]["created_at"]
    assert state[0]["content_hash"] == country_publish_hash(row["content_hash"] for row in replacement)
    assert state[0]["series_count"] == state[0]["point_count"] == 2
    counts = await apply(maker, [], water=water)
    assert counts["deleted"] == 2
    curves, state = await snapshot(maker)
    assert curves == [] and state[0]["series_count"] == state[0]["point_count"] == 0


async def test_projection_version_change_rewrites_once(projection_db):
    maker = projection_db
    rows = [curve("D001")]
    await apply(maker, rows)
    async with maker.begin() as db:
        await db.execute(text("UPDATE public_disease_series SET projection_version='previous'"))
    assert (await apply(maker, rows))["changed"] == 1
    assert (await apply(maker, rows))["unchanged"] == 1


async def test_invalid_candidates_and_outer_rollback_preserve_publication(projection_db):
    maker = projection_db
    await apply(maker, [curve("D001"), curve("D002")])
    before = await snapshot(maker)
    for bad in ([curve("D001"), curve("D001")], [curve("D001", country="US")], [curve("UNKNOWN")]):
        with pytest.raises((ValueError, RuntimeError)):
            await apply(maker, bad)
        assert await snapshot(maker) == before
    with pytest.raises(RuntimeError, match="ack failed"):
        async with maker.begin() as db:
            await _replace_country_rows(db, "CA", [curve("D001", 99)], source_high_water=None,
                                        stored_disease_ids={"D001", "D002"})
            raise RuntimeError("ack failed")
    assert await snapshot(maker) == before
