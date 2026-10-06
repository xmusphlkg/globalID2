"""Process lifecycle helpers for standalone literature maintenance commands."""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO, TypeVar

from src.core.database import dispose_database

T = TypeVar("T")


class ConcurrentApplyError(RuntimeError):
    """Another process already owns the command's apply lock."""


@contextmanager
def exclusive_apply_lock(path: Path, *, operation: str) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock file: unlinking it could let another process lock a new
    # inode while an existing waiter still holds the original one.
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConcurrentApplyError(
                f"another {operation} --apply process is already running"
            ) from exc
        yield handle


def run_maintenance(operation: Callable[[], Awaitable[T]]) -> T:
    """Dispose async database connections before their event loop closes."""

    async def run() -> T:
        try:
            return await operation()
        finally:
            await dispose_database()

    return asyncio.run(run())
