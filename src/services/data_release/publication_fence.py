"""Cooperative, cross-host publication coordination; not an external fence.

The token is useful only where the receiver atomically rejects stale tokens.
Cloudflare Pages deploy does not consume it. Consequently this coordinator is
not wired to live releases and cannot protect against old or direct deployers.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from sqlalchemy import text

LOCK_NAME = "globalid:papers-pages-publication:v1"


class PublicationFenceError(RuntimeError):
    pass


@dataclass
class PublicationFence:
    connection: Any
    epoch: int
    backend_pid: int
    active: bool = True

    async def assert_current(self) -> None:
        if not self.active:
            raise PublicationFenceError("publication_fence_inactive")
        try:
            result = await self.connection.execute(text(
                "SELECT pg_backend_pid(), epoch FROM publication_fences WHERE name = :name"
            ), {"name": LOCK_NAME})
            row = result.one()
            if row[0] != self.backend_pid or row[1] != self.epoch:
                raise PublicationFenceError("publication_fence_lost")
        except PublicationFenceError:
            self.active = False
            raise
        except Exception as exc:
            self.active = False
            raise PublicationFenceError("publication_fence_lost") from exc


@asynccontextmanager
async def acquire_publication_fence(engine: Any) -> AsyncIterator[PublicationFence]:
    """Hold a PostgreSQL session advisory lock and persist an increasing epoch.

    Use a session-pooling connection to the same primary database for every
    participating publisher. Transaction pooling, independent databases,
    nonparticipants and unfenced external operations invalidate the guarantee.
    A connection loss invalidates the handle; this is not a time-based lease.
    """
    if getattr(getattr(engine, "dialect", None), "name", None) != "postgresql":
        raise PublicationFenceError("publication_fence_postgres_required")
    async with engine.connect() as conn:
        attempted = False
        fence = None
        try:
            attempted = True
            result = await conn.execute(text(
                "SELECT pg_try_advisory_lock(hashtextextended(:name, 0)), pg_backend_pid()"
            ), {"name": LOCK_NAME})
            row = result.one()
            if row[0] is not True:
                raise PublicationFenceError("publication_fence_busy")
            pid = row[1]
            result = await conn.execute(text(
                "INSERT INTO publication_fences(name, epoch) VALUES (:name, 1) "
                "ON CONFLICT (name) DO UPDATE SET epoch = publication_fences.epoch + 1 "
                "RETURNING epoch"
            ), {"name": LOCK_NAME})
            epoch = result.scalar_one()
            await conn.commit()
            fence = PublicationFence(conn, epoch, pid)
            await fence.assert_current()
            yield fence
        finally:
            if fence is not None:
                fence.active = False
            if attempted:
                # Session locks survive rollback and pool return. Discard the
                # session even if cancellation or an unlock failure occurs.
                await conn.invalidate()
