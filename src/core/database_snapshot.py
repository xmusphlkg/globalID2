"""Share one PostgreSQL read view across release export subprocesses.

The owner stays open only while exporting. Children import its snapshot at the
start of every transaction; PostgreSQL enforces read-only access. Never put the
snapshot variable into the worker's environment or reuse it for publication.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import re

from sqlalchemy import event, text

SNAPSHOT_ENV = "GLOBALID_RELEASE_DB_SNAPSHOT"
_SNAPSHOT_ID = re.compile(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+\Z")


def validate_snapshot_id(value: str) -> str:
    # SET TRANSACTION SNAPSHOT requires a SQL string literal, not a bind value.
    if not _SNAPSHOT_ID.fullmatch(value):
        raise ValueError("Invalid PostgreSQL release snapshot identifier")
    return value


def bind_read_snapshot(session, snapshot_id: str) -> None:
    snapshot_id = validate_snapshot_id(snapshot_id)

    def begin(_session, transaction, connection):
        if transaction.nested:
            return
        if connection.dialect.name != "postgresql":
            raise RuntimeError("Release read snapshots require PostgreSQL")
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text(f"SET TRANSACTION SNAPSHOT '{snapshot_id}'"))

    # Reapply after an explicit commit/rollback, not just the first query.
    event.listen(session.sync_session, "after_begin", begin)


@asynccontextmanager
async def export_read_snapshot(engine=None):
    if engine is None:
        from .database import get_engine
        engine = get_engine()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Release read snapshots require PostgreSQL")
    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            snapshot_id = validate_snapshot_id(
                (await connection.execute(text("SELECT pg_export_snapshot()"))).scalar_one()
            )
            yield {SNAPSHOT_ENV: snapshot_id}
