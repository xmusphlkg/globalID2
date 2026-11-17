"""Keep foreground ingestion tasks live without claiming a worker singleton."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from src.core.database import get_db
from src.core.logging import get_logger
from src.core.task_manager import task_manager
from src.domain import Task
from src.domain.task import TaskStatus

logger = get_logger(__name__)
HEARTBEAT_SECONDS = 30.0


class ForegroundTaskLeaseLostError(RuntimeError):
    """The foreground process must stop without changing another owner's task."""


@dataclass(frozen=True)
class ForegroundTaskLease:
    task_uuid: str
    owner: str

    async def owned_task(self, db):
        """Lock and fence a terminal update in the caller's DB transaction."""
        task = (await db.execute(select(Task).where(
            Task.task_uuid == self.task_uuid).with_for_update())).scalar_one_or_none()
        metadata = dict(task.metadata_ or {}) if task is not None else {}
        if (task is None or task.status != TaskStatus.RUNNING
            or (metadata.get("task_lease") or {}).get("owner") != self.owner
            or metadata.get("cancel_requested")):
            raise ForegroundTaskLeaseLostError(f"Foreground task ownership lost: {self.task_uuid}")
        return task


async def _claim(lease):
    async with get_db() as db:
        task = (await db.execute(select(Task).where(
            Task.task_uuid == lease.task_uuid).with_for_update())).scalar_one_or_none()
        if task is None or task.status != TaskStatus.RUNNING:
            raise ForegroundTaskLeaseLostError("Foreground leases require an existing RUNNING task")
        metadata = dict(task.metadata_ or {})
        current = dict(metadata.get("task_lease") or {})
        if current.get("owner") and not current.get("released_at"):
            raise ForegroundTaskLeaseLostError("Task already has an unreleased execution owner")
        if metadata.get("cancel_requested"):
            raise ForegroundTaskLeaseLostError("Task cancellation has already been requested")
        now = datetime.now(timezone.utc).isoformat()
        metadata["task_lease"] = {"owner": lease.owner, "claimed_at": now, "heartbeat_at": now}
        task.metadata_ = metadata


async def _release(lease):
    async with get_db() as db:
        task = (await db.execute(select(Task).where(
            Task.task_uuid == lease.task_uuid).with_for_update())).scalar_one_or_none()
        if task is None:
            return False
        metadata = dict(task.metadata_ or {})
        current = dict(metadata.get("task_lease") or {})
        if current.get("owner") != lease.owner:
            return False
        if not current.get("released_at"):
            current["released_at"] = datetime.now(timezone.utc).isoformat()
            metadata["task_lease"] = current
            task.metadata_ = metadata
        return True


@asynccontextmanager
async def foreground_task_lease(task_uuid: str, *, heartbeat_seconds: float = HEARTBEAT_SECONDS):
    """Lease executing foreground work; leave task status to the caller.

    Wrap collection/organization/storage. The yielded owner's ``owned_task``
    method fences subsequent status writes. Ownership loss cancels the active
    body and raises ForegroundTaskLeaseLostError; it never fails or releases
    another owner's task. Blocking source calls should run via ``to_thread``.
    """
    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")
    lease = ForegroundTaskLease(task_uuid, "foreground:" + str(uuid4()))
    await _claim(lease)
    body_task = asyncio.current_task()
    lost = False

    async def renew():
        nonlocal lost
        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                owned = await task_manager.heartbeat_task_lease(task_uuid, lease.owner)
            except Exception as exc:
                logger.warning("Foreground task heartbeat failed for {}: {}", task_uuid, exc)
                continue
            if not owned:
                lost = True
                body_task.cancel()
                return

    heartbeat = asyncio.create_task(renew(), name=f"foreground-lease:{task_uuid}")
    error = None
    try:
        yield lease
    except BaseException as exc:
        error = exc
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        owned = await _release(lease)
    if lost or not owned:
        if lost:
            body_task.uncancel()  # remove only the cancellation issued above
        raise ForegroundTaskLeaseLostError(f"Foreground task ownership lost: {task_uuid}") from error
    if error is not None:
        raise error
