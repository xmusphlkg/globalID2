"""Optional short database barriers and collection scope tracking.

Normal releases use PostgreSQL read snapshots and never own this barrier for
their task lifetime. The explicit publication context remains available for
operations that really require draining cooperative database scopes.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from src.domain import TaskType
COLLECTION_STORE_TASK_TYPES = frozenset({
    TaskType.CRAWL_DATA,
    TaskType.PROCESS_DATA,
    TaskType.SYNC_LITERATURE,
    TaskType.REFRESH_SITUATION_SOURCES,
    TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
    TaskType.UPDATE_DISEASE_KNOWLEDGE,
})


@dataclass
class _DatabaseAdmission:
    scopes: int = 0


_writer_gate: ContextVar[ReleaseAdmission | None] = ContextVar("release_writer_gate", default=None)
_database_admission: ContextVar[_DatabaseAdmission | None] = ContextVar("release_database_admission", default=None)


class ReleaseAdmission:
    def __init__(self) -> None:
        self.cooperative_tasks: set[str] = set()
        self._condition = asyncio.Condition()
        self._active_scopes = 0
        self._waiting_releases = 0
        self._publishing = False

    @contextmanager
    def writer(self, task_uuid: str):
        """Propagate admission to child coroutines and source capture threads."""
        token = _writer_gate.set(self)
        self.cooperative_tasks.add(task_uuid)
        try:
            yield
        finally:
            self.cooperative_tasks.discard(task_uuid)
            _writer_gate.reset(token)

    @asynccontextmanager
    async def database_scope(self):
        admission = _database_admission.get()
        async with self._condition:
            # Nested sessions (including awaited child tasks) must be able to
            # finish the admitted unit, or draining would deadlock. A child
            # inheriting an already-closed scope must obtain fresh admission.
            if admission is None or admission.scopes == 0:
                await self._condition.wait_for(
                    lambda: not self._waiting_releases and not self._publishing
                )
                admission = _DatabaseAdmission()
            admission.scopes += 1
            self._active_scopes += 1
        token = _database_admission.set(admission)
        try:
            yield
        finally:
            _database_admission.reset(token)
            async with self._condition:
                admission.scopes -= 1
                self._active_scopes -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def publication(self):
        async with self._condition:
            self._waiting_releases += 1
            try:
                await self._condition.wait_for(
                    lambda: not self._active_scopes and not self._publishing
                )
                self._publishing = True
            finally:
                self._waiting_releases -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                self._publishing = False
                self._condition.notify_all()

    @asynccontextmanager
    async def task_scope(
        self, task_uuid: str, task_type: TaskType, *,
        reports_only: bool = False, raw_archive_only: bool = False,
    ):
        if task_type in COLLECTION_STORE_TASK_TYPES:
            with self.writer(task_uuid):
                yield
        else:
            yield


@asynccontextmanager
async def collection_database_scope():
    gate = _writer_gate.get()
    if gate is None:
        yield
    else:
        async with gate.database_scope():
            yield


release_admission = ReleaseAdmission()
