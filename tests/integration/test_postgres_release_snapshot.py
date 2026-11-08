"""Opt-in PostgreSQL isolation tests; only a unique temporary schema is written.

Run with TEST_SNAPSHOT_DATABASE_URL set to an asyncpg test connection URL.
"""
import asyncio
import os
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.core.database_snapshot import SNAPSHOT_ENV, export_read_snapshot


@pytest.fixture
async def snapshot_database(monkeypatch):
    url = os.environ.get("TEST_SNAPSHOT_DATABASE_URL")
    if not url:
        pytest.skip("TEST_SNAPSHOT_DATABASE_URL is required")
    engine = create_async_engine(url)
    schema = "test_release_snapshot_" + uuid4().hex
    table = schema + ".facts"
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
        await connection.execute(text(f"CREATE TABLE {table} (id integer PRIMARY KEY, value integer)"))
        await connection.execute(text(f"INSERT INTO {table} VALUES (1, 10)"))
    monkeypatch.setattr(database, "_session_maker", async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield engine, table, url
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await engine.dispose()


async def test_concurrent_writes_commit_while_every_reader_retains_same_snapshot(snapshot_database, monkeypatch):
    engine, table, url = snapshot_database
    async with asyncio.timeout(30):
        async with export_read_snapshot(engine) as environment:
            monkeypatch.setenv(SNAPSHOT_ENV, environment[SNAPSHOT_ENV])
            # Raw writer does not inherit a reader-only environment. This is
            # the same separation as the worker and its export subprocesses.
            async with engine.begin() as connection:
                await connection.execute(text(f"UPDATE {table} SET value=20 WHERE id=1"))

            async def read():
                async with database.get_db() as session:
                    first = (await session.execute(text(f"SELECT value FROM {table}"))).scalar_one()
                    await session.commit()
                    second = (await session.execute(text(f"SELECT value FROM {table}"))).scalar_one()
                    return first, second
            assert await asyncio.gather(read(), read()) == [(10, 10), (10, 10)]

            # A fresh process imports the same view, including the production
            # get_db entry point and its environment-to-transaction plumbing.
            script = """
import asyncio, os
from sqlalchemy import text
from src.core.database import get_db, dispose_database
async def main():
    async with get_db() as db:
        value = (await db.execute(text('SELECT value FROM ' + os.environ['TEST_RELEASE_TABLE']))).scalar_one()
        print('SNAPSHOT_VALUE=' + str(value))
    await dispose_database()
asyncio.run(main())
"""
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", script,
                env={**os.environ, **environment, "DATABASE_URL": url, "TEST_RELEASE_TABLE": table},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, errors = await proc.communicate()
                assert proc.returncode == 0, errors.decode()
                assert b"SNAPSHOT_VALUE=10" in output
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
        monkeypatch.delenv(SNAPSHOT_ENV)
        async with database.get_db() as session:
            assert (await session.execute(text(f"SELECT value FROM {table}"))).scalar_one() == 20


async def test_snapshot_rejects_writes_and_never_falls_back_after_owner_closes(snapshot_database, monkeypatch):
    engine, table, _ = snapshot_database
    async with export_read_snapshot(engine) as environment:
        monkeypatch.setenv(SNAPSHOT_ENV, environment[SNAPSHOT_ENV])
        with pytest.raises(Exception, match="read-only"):
            async with database.get_db() as session:
                await session.execute(text(f"UPDATE {table} SET value=99"))
        async with database.get_db() as session:
            assert (await session.execute(text(f"SELECT value FROM {table}"))).scalar_one() == 10
    with pytest.raises(Exception, match="invalid snapshot"):
        async with database.get_db() as session:
            await session.execute(text(f"SELECT value FROM {table}"))
    monkeypatch.delenv(SNAPSHOT_ENV)


async def test_cancelled_snapshot_owner_releases_its_transaction(snapshot_database):
    engine, _, _ = snapshot_database
    ready = asyncio.Event()
    async def owner():
        async with export_read_snapshot(engine):
            ready.set()
            await asyncio.Event().wait()
    work = asyncio.create_task(owner())
    await asyncio.wait_for(ready.wait(), timeout=5)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert engine.pool.checkedout() == 0
