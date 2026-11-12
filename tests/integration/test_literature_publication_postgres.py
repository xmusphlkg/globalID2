"""Concurrent publication cannot acknowledge the same change twice."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.collection import PublishDirty
from src.services.data_release.global_selection import acknowledge_global_selection


async def test_concurrent_publisher_waits_and_rejects_a_consumed_marker():
    url = os.environ.get("TEST_AI_DATABASE_URL")
    if not url:
        pytest.skip("TEST_AI_DATABASE_URL must be a disposable PostgreSQL database")
    schema = "research_release_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: PublishDirty.__table__.create(sync))
        async with maker() as db:
            row = PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc))
            db.add(row)
            await db.commit()
            marker_id = row.id
        async with maker() as first, maker() as second:
            await acknowledge_global_selection(first, [marker_id], line="literature", task_uuid="first")
            async def competing():
                with pytest.raises(ValueError, match="already consumed"):
                    await acknowledge_global_selection(second, [marker_id], line="literature", task_uuid="second")
            rival = asyncio.create_task(competing())
            await asyncio.sleep(0.1)
            assert not rival.done()
            await first.commit()
            await asyncio.wait_for(rival, 5)
            await second.rollback()
    finally:
        await engine.dispose()
        async with control.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()
