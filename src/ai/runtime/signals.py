"""Targeted activity notifications with a batched database reconciliation path.

Notifications are hints, never the result store. Register before reading state;
clear the event before each read so a concurrent commit cannot lose a wakeup.
One reconciler serves every waiter, including results from other executors when
PostgreSQL LISTEN is reconnecting. No shared lock is held across database I/O.
"""

from __future__ import annotations

import asyncio
import json
import weakref
from collections import defaultdict
from contextlib import asynccontextmanager

from sqlalchemy import select, text

from src.core import get_logger
from src.domain import Task, TaskStatus
from src.domain.ai_work_item import AIWorkItem

from .contracts import RUNTIME_PARENT, TERMINAL_STATUSES

logger = get_logger(__name__)
CHANNEL = "globalid_ai_activity"
RECONCILE_SECONDS = 2.0
BATCH_SIZE = 256


async def publish_changes(db, work_ids=(), *, parent: str | None = None) -> None:
    """Postgres delivers this hint only if the surrounding transaction commits."""
    if db.get_bind().dialect.name != "postgresql":
        return
    ids = list(work_ids)
    for offset in range(0, max(1, len(ids)), BATCH_SIZE):
        await db.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {
                "channel": CHANNEL,
                "payload": json.dumps(
                    {"work": ids[offset : offset + BATCH_SIZE], "parent": parent}
                ),
            },
        )


class ActivitySignals:
    def __init__(self, database, *, interval: float = RECONCILE_SECONDS):
        self.database = database
        self.interval = interval
        self._events: dict[int, set[asyncio.Event]] = defaultdict(set)
        self._parents: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._tasks: list[asyncio.Task] = []
        self._changed = asyncio.Event()

    def notify(self, work_ids=(), *, parent: str | None = None) -> None:
        events = set(self._parents.get(parent, ())) if parent else set()
        for work_id in work_ids:
            events.update(self._events.get(int(work_id), ()))
        for event in events:
            event.set()

    @asynccontextmanager
    async def subscribe(self, work_ids, parent: str):
        ids = set(work_ids)
        event = asyncio.Event()
        for work_id in ids:
            self._events[work_id].add(event)
        self._parents[parent].add(event)
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._reconcile(), name="ai-activity-reconcile"),
                asyncio.create_task(self._listen(), name="ai-activity-listener"),
            ]
        self._changed.set()
        try:
            yield event
        finally:
            for work_id in ids:
                subscribers = self._events.get(work_id)
                if subscribers is not None:
                    subscribers.discard(event)
                    if not subscribers:
                        del self._events[work_id]
            subscribers = self._parents.get(parent)
            if subscribers is not None:
                subscribers.discard(event)
                if not subscribers:
                    del self._parents[parent]
            if not self._parents:
                tasks, self._tasks = self._tasks, []
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _scan(self) -> None:
        ids = list(self._events)
        for offset in range(0, len(ids), BATCH_SIZE):
            batch = ids[offset : offset + BATCH_SIZE]
            async with self.database() as db:
                rows = (
                    await db.execute(
                        select(
                            AIWorkItem.id,
                            AIWorkItem.status,
                            AIWorkItem.parent_task_uuid,
                            Task.status,
                            Task.metadata_["cancel_requested"].as_boolean(),
                        )
                        .outerjoin(Task, Task.task_uuid == AIWorkItem.parent_task_uuid)
                        .where(AIWorkItem.id.in_(batch))
                    )
                ).all()
            changed = {
                work_id
                for work_id, status, parent, parent_status, cancelled in rows
                if status in TERMINAL_STATUSES
                or parent_status
                in {TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.COMPLETED}
                or cancelled
                or (parent_status is None and parent != RUNTIME_PARENT)
            }
            changed.update(set(batch) - {row[0] for row in rows})
            self.notify(changed)

    async def _reconcile(self) -> None:
        while True:
            self._changed.clear()
            try:
                await self._scan()
            except Exception as exc:
                logger.warning("Activity reconciliation failed: {}", type(exc).__name__)
            try:
                await asyncio.wait_for(self._changed.wait(), self.interval)
            except asyncio.TimeoutError:
                pass

    def _received(self, _connection, _pid, _channel, payload: str) -> None:
        try:
            message = json.loads(payload)
            self.notify(message.get("work", ()), parent=message.get("parent"))
        except (ValueError, TypeError, AttributeError):
            self._changed.set()

    async def _listen(self) -> None:
        import asyncpg

        while True:
            connection = None
            try:
                # Resolve the connection inside the retry loop too. A pool
                # outage at startup must not permanently disable notifications.
                async with self.database() as db:
                    bind = db.get_bind()
                    if bind.dialect.name != "postgresql":
                        return
                    url = bind.engine.url.set(drivername="postgresql").render_as_string(
                        hide_password=False
                    )
                connection = await asyncpg.connect(url, timeout=5)
                await connection.add_listener(CHANNEL, self._received)
                # LISTEN is installed before the recovery read, closing the
                # registration race and covering notifications lost on reconnect.
                self._changed.set()
                while not connection.is_closed():
                    await asyncio.sleep(5)
            except Exception as exc:
                logger.warning("Activity listener reconnecting: {}", type(exc).__name__)
            finally:
                if connection is not None:
                    try:
                        await connection.close(timeout=2)
                    except Exception as exc:
                        logger.warning(
                            "Activity listener close failed: {}", type(exc).__name__
                        )
            await asyncio.sleep(5)


_hubs: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def activity_signals(database) -> ActivitySignals:
    loop = asyncio.get_running_loop()
    current = _hubs.get(loop)
    if current is None or current.database is not database:
        current = _hubs[loop] = ActivitySignals(database)
    return current
