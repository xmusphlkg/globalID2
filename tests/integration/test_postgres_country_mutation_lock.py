"""Opt-in real PostgreSQL lock-order tests; no application tables are written.

TEST_MUTATION_DATABASE_URL must point at a disposable PostgreSQL database.
Every test also uses unique advisory keys, never the live application's keys.
"""
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import disease_mutation_lock as locks


@pytest.fixture
async def mutation_database(monkeypatch):
    url = os.environ.get("TEST_MUTATION_DATABASE_URL")
    if not url:
        pytest.skip("TEST_MUTATION_DATABASE_URL is required")
    namespace = uuid4().hex
    monkeypatch.setattr(locks, "DISEASE_DATA_MUTATION_LOCK_KEY", f"test:mutation:{namespace}")
    monkeypatch.setattr(locks, "COUNTRY_DATA_MUTATION_LOCK_NAMESPACE", f"test:country:{namespace}")
    engine = create_async_engine(url, pool_size=6, max_overflow=0)
    try:
        yield engine, async_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _backend_pid(session):
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _wait_until_blocked(engine, pid):
    async with asyncio.timeout(5):
        while True:
            async with engine.connect() as connection:
                blockers = (await connection.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}
                )).scalar_one()
            if blockers:
                return
            await asyncio.sleep(0.01)


async def test_distinct_countries_progress_while_parent_and_child_serialize(mutation_database):
    engine, sessions = mutation_database
    async with sessions() as us, sessions() as se, sessions() as child:
        await locks.acquire_country_data_mutation_lock(us, "US")
        async with asyncio.timeout(5):
            await locks.acquire_country_data_mutation_lock(se, "SE")
        child_pid = await _backend_pid(child)
        waiting = asyncio.create_task(locks.acquire_country_data_mutation_lock(child, "US-AK"))
        try:
            await _wait_until_blocked(engine, child_pid)
            assert not waiting.done()
            await us.commit()
            await asyncio.wait_for(waiting, 5)
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await se.rollback()
            await child.rollback()


async def test_maintenance_waits_for_all_country_transactions(mutation_database):
    engine, sessions = mutation_database
    async with sessions() as us, sessions() as se, sessions() as maintenance:
        await locks.acquire_country_data_mutation_lock(us, "US")
        await locks.acquire_country_data_mutation_lock(se, "SE")
        pid = await _backend_pid(maintenance)
        waiting = asyncio.create_task(locks.acquire_disease_data_mutation_lock(maintenance))
        try:
            await _wait_until_blocked(engine, pid)
            await us.rollback()
            # The second country's open transaction still excludes maintenance.
            await _wait_until_blocked(engine, pid)
            assert not waiting.done()
            await se.commit()
            await asyncio.wait_for(waiting, 5)
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await maintenance.rollback()


async def test_exclusive_maintenance_blocks_countries_until_rollback(mutation_database):
    engine, sessions = mutation_database
    async with sessions() as maintenance, sessions() as country:
        await locks.acquire_disease_data_mutation_lock(maintenance)
        pid = await _backend_pid(country)
        waiting = asyncio.create_task(locks.acquire_country_data_mutation_lock(country, "JP-13"))
        try:
            await _wait_until_blocked(engine, pid)
            assert not waiting.done()
            await maintenance.rollback()
            await asyncio.wait_for(waiting, 5)
            # Re-entry uses the same transaction and never upgrades the gate.
            await locks.acquire_country_data_mutation_lock(country, "JP")
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await country.rollback()


async def test_cancelled_waiter_releases_shared_gate_when_transaction_closes(mutation_database):
    engine, sessions = mutation_database
    async with sessions() as owner, sessions() as waiter:
        await locks.acquire_country_data_mutation_lock(owner, "CA")
        pid = await _backend_pid(waiter)
        waiting = asyncio.create_task(locks.acquire_country_data_mutation_lock(waiter, "CA-ON"))
        await _wait_until_blocked(engine, pid)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await waiter.rollback()
        await owner.rollback()
    async with sessions() as maintenance:
        async with asyncio.timeout(5):
            await locks.acquire_disease_data_mutation_lock(maintenance)


async def test_maintenance_can_call_country_store_without_lock_upgrade(mutation_database):
    _, sessions = mutation_database
    async with sessions() as maintenance:
        await locks.acquire_disease_data_mutation_lock(maintenance)
        async with asyncio.timeout(5):
            await locks.acquire_country_data_mutation_lock(maintenance, "KR")
