import pytest
import os
import uuid

from src.services.data_release.publication_fence import (
    PublicationFenceError, acquire_publication_fence,
)


class Result:
    def __init__(self, value): self.value = value
    def one(self): return self.value
    def scalar_one(self): return self.value


class SimulatedPostgres:
    """Model the two operations shared across independent connections."""
    dialect = type("Dialect", (), {"name": "postgresql"})()
    def __init__(self):
        self.owner = None
        self.epoch = 0
        self.pid = 0
        self.connections = []
    def connect(self):
        self.pid += 1
        conn = Connection(self, self.pid)
        self.connections.append(conn)
        return conn


class Connection:
    def __init__(self, db, pid):
        self.db, self.pid, self.closed = db, pid, False
        self.fail_after_lock = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def execute(self, query, params):
        assert not self.closed
        sql = str(query)
        if "pg_try_advisory_lock" in sql:
            if self.db.owner is not None:
                return Result((False, self.pid))
            self.db.owner = self.pid
            if self.fail_after_lock:
                raise OSError("lost response after server acquired lock")
            return Result((True, self.pid))
        if sql.startswith("INSERT"):
            self.db.epoch += 1
            return Result(self.db.epoch)
        return Result((self.pid, self.db.epoch))
    async def commit(self): pass
    async def invalidate(self):
        self.closed = True
        if self.db.owner == self.pid:
            self.db.owner = None


@pytest.mark.asyncio
async def test_cross_connection_contention_release_and_monotonic_epoch():
    db = SimulatedPostgres()
    async with acquire_publication_fence(db) as first:
        assert first.epoch == 1
        await first.assert_current()
        with pytest.raises(PublicationFenceError, match="busy"):
            async with acquire_publication_fence(db):
                pass
        assert db.owner == first.backend_pid
    assert db.owner is None
    with pytest.raises(PublicationFenceError, match="inactive"):
        await first.assert_current()
    async with acquire_publication_fence(db) as second:
        assert second.epoch == 2


@pytest.mark.asyncio
async def test_interrupted_acquisition_discards_session_even_if_result_is_unknown():
    db = SimulatedPostgres()
    original_connect = db.connect
    def connect():
        conn = original_connect()
        conn.fail_after_lock = True
        return conn
    db.connect = connect
    with pytest.raises(OSError):
        async with acquire_publication_fence(db):
            pass
    assert db.owner is None
    assert db.connections[-1].closed


@pytest.mark.asyncio
async def test_lost_or_superseded_session_fails_closed():
    db = SimulatedPostgres()
    async with acquire_publication_fence(db) as fence:
        db.epoch += 1
        with pytest.raises(PublicationFenceError, match="lost"):
            await fence.assert_current()
        with pytest.raises(PublicationFenceError, match="inactive"):
            await fence.assert_current()


@pytest.mark.asyncio
async def test_requires_postgres():
    engine = type("Engine", (), {"dialect": type("Dialect", (), {"name": "sqlite"})()})()
    with pytest.raises(PublicationFenceError, match="postgres_required"):
        async with acquire_publication_fence(engine):
            pass


@pytest.mark.asyncio
async def test_real_postgres_coordinator_when_test_database_is_explicitly_configured():
    """Use a throwaway schema in an explicitly supplied test database."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ.get("TEST_PUBLICATION_FENCE_DATABASE_URL")
    if not url:
        pytest.skip("TEST_PUBLICATION_FENCE_DATABASE_URL is required for PostgreSQL contention test")
    schema = "publication_fence_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    engine = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'CREATE TABLE "{schema}".publication_fences '
                                    '(name varchar(128) primary key, epoch bigint not null CHECK (epoch > 0))'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        async with acquire_publication_fence(engine) as first:
            await first.assert_current()
            with pytest.raises(PublicationFenceError, match="busy"):
                async with acquire_publication_fence(engine):
                    pass
        async with acquire_publication_fence(engine) as second:
            assert second.epoch == first.epoch + 1
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
