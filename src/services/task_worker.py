"""Standalone worker process for executing queued tasks.

This worker decouples task execution from the API process. The API only marks tasks
as queued, and this worker continuously polls and executes them.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
from datetime import datetime, timezone
import gc
import signal
import sys
from typing import Optional

from sqlalchemy import case, select

from src.core import get_database, get_logger
from src.core.config import get_config
from src.domain import Task, TaskPriority, TaskStatus
from src.services.task_executor import execute_task, recover_interrupted_tasks_on_startup
from src.services._lifecycle import safe_exception_summary
from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.task_manager import task_manager

logger = get_logger(__name__)

TASK_WORKER_CONFIG = get_config().task_worker
POLL_INTERVAL_SECONDS = TASK_WORKER_CONFIG.poll_interval_seconds
IDLE_LOG_EVERY = TASK_WORKER_CONFIG.idle_log_every
MAX_CONCURRENT_TASKS = TASK_WORKER_CONFIG.concurrency
TASK_HEARTBEAT_SECONDS = TASK_WORKER_CONFIG.task_heartbeat_seconds
STALE_TASK_SECONDS = TASK_WORKER_CONFIG.stale_task_seconds
RECOVERY_SCAN_SECONDS = TASK_WORKER_CONFIG.recovery_scan_seconds
RUNTIME_LEASE_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_lease_ttl_seconds
RUNTIME_HEARTBEAT_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_heartbeat_ttl_seconds

_LIBC: ctypes.CDLL | None = None
_LIBC_LOOKUP_DONE = False


def _release_task_memory() -> None:
    """Return cyclic garbage and idle libc heap pages after a task finishes."""
    global _LIBC, _LIBC_LOOKUP_DONE

    gc.collect()
    if sys.platform != "linux":
        return

    if not _LIBC_LOOKUP_DONE:
        _LIBC_LOOKUP_DONE = True
        libc_name = ctypes.util.find_library("c")
        if libc_name:
            try:
                _LIBC = ctypes.CDLL(libc_name)
                _LIBC.malloc_trim.argtypes = [ctypes.c_size_t]
                _LIBC.malloc_trim.restype = ctypes.c_int
            except Exception as exc:
                _LIBC = None
                logger.debug("libc malloc_trim unavailable: {}", exc)

    if _LIBC is None:
        return

    try:
        _LIBC.malloc_trim(0)
    except Exception as exc:
        logger.debug("libc malloc_trim failed: {}", exc)


async def _claim_next_task_uuid(worker_instance_id: str) -> Optional[str]:
    """Pick one runnable task and return its UUID.

    Claim must be atomic across worker processes. We lock one candidate row,
    mark it RUNNING in the same transaction, and then return the UUID.
    """
    async with get_database() as db:
        priority_rank = case(
            (Task.priority == TaskPriority.URGENT, 0),
            (Task.priority == TaskPriority.HIGH, 1),
            (Task.priority == TaskPriority.NORMAL, 2),
            else_=3,
        )
        result = await db.execute(
            select(Task)
            .where(Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]))
            .order_by(priority_rank.asc(), Task.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None

        now = datetime.now(timezone.utc)
        metadata = dict(task.metadata_ or {})
        metadata["task_lease"] = {
            "owner": worker_instance_id,
            "claimed_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        task.metadata_ = metadata
        task.status = TaskStatus.RUNNING
        task.last_error = None
        await db.commit()
        return task.task_uuid


async def _heartbeat_claimed_task(
    task_uuid: str,
    worker_instance_id: str,
    finished: asyncio.Event,
) -> None:
    """Keep the task lease fresh until execution reaches a terminal state."""
    while not finished.is_set():
        try:
            await asyncio.wait_for(finished.wait(), timeout=TASK_HEARTBEAT_SECONDS)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            owned = await task_manager.heartbeat_task_lease(
                task_uuid, worker_instance_id
            )
        except Exception as exc:
            # A short database outage must not terminate the task coroutine.
            # If it lasts past STALE_TASK_SECONDS, the worker singleton lease
            # still prevents a second worker from recovering this live task.
            logger.warning("Task heartbeat failed for {}: {}", task_uuid, exc)
            continue
        if not owned:
            # A normal terminal status releases the task lease before this
            # coroutine observes ``finished``.  Treat that race as an
            # informational stop; ownership safety is still enforced by the
            # conditional heartbeat update itself.
            logger.info(
                "Task lease heartbeat ended (terminal or ownership changed); task_uuid={} owner={}",
                task_uuid,
                worker_instance_id,
            )
            return


async def _recover_stale_tasks(
    stop_event: asyncio.Event, worker_instance_id: str
) -> None:
    """Continuously recover tasks orphaned after a hard worker termination."""
    while not stop_event.is_set():
        try:
            recovered = await recover_interrupted_tasks_on_startup(
                stale_after_seconds=STALE_TASK_SECONDS,
                exclude_owner=worker_instance_id,
            )
            if recovered:
                logger.warning("Recovered {} stale task(s) during worker sweep", recovered)
        except Exception as exc:
            logger.exception("Stale-task recovery sweep failed: {}", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECOVERY_SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run_worker() -> None:
    """Main worker loop."""
    logger.info(
        "Task worker started (poll_interval={}s, concurrency={})",
        POLL_INTERVAL_SECONDS,
        MAX_CONCURRENT_TASKS,
    )

    instance_id = runtime_registry.new_instance_id("worker")
    if not await runtime_registry.acquire_lease(
        "worker", instance_id, ttl_seconds=RUNTIME_LEASE_TTL_SECONDS
    ):
        raise RuntimeError(
            "Another task worker owns the singleton lease or Redis is unavailable"
        )
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)

    recovered = await recover_interrupted_tasks_on_startup(
        stale_after_seconds=STALE_TASK_SECONDS
    )
    if recovered:
        logger.warning("Recovered {} interrupted task(s) on worker startup", recovered)

    stop_event = asyncio.Event()
    lease_lost = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _threaded_lease_lost() -> None:
        loop.call_soon_threadsafe(lease_lost.set)
        loop.call_soon_threadsafe(stop_event.set)

    runtime_guard = runtime_registry.start_threaded_guard(
        "worker",
        instance_id,
        lease_ttl_seconds=RUNTIME_LEASE_TTL_SECONDS,
        heartbeat_ttl_seconds=RUNTIME_HEARTBEAT_TTL_SECONDS,
        metadata={"concurrency": MAX_CONCURRENT_TASKS},
        on_lease_lost=_threaded_lease_lost,
    )
    recovery_sweep = asyncio.create_task(
        _recover_stale_tasks(stop_event, instance_id),
        name="control-plane-worker-task-recovery",
    )
    await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)

    def _request_stop() -> None:
        logger.info("Stop signal received, task worker shutting down soon")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Fallback for environments that do not support signal handlers.
            pass

    async def _run_claimed_task(task_uuid: str) -> None:
        task_logger = logger.bind(task_uuid=task_uuid, worker_instance_id=instance_id)
        finished = asyncio.Event()
        task_heartbeat = asyncio.create_task(
            _heartbeat_claimed_task(task_uuid, instance_id, finished),
            name=f"task-heartbeat-{task_uuid}",
        )
        try:
            task_logger.info("Executing claimed task")
            await control_plane_events.publish(
                "task.claimed",
                resource_type="task",
                resource_id=task_uuid,
                data={"worker_instance_id": instance_id},
            )
            await execute_task(task_uuid)
        except Exception as exc:
            task_logger.exception("Worker failed executing task: {}", exc)
            error_summary = safe_exception_summary(exc)
            try:
                if await task_manager.fail_owned_task_lease(
                    task_uuid, instance_id, error_summary
                ):
                    await task_manager.add_workbook_entry(
                        task_uuid,
                        entry_type="error",
                        title="Worker Execution Failed",
                        content=error_summary,
                        content_type="text",
                    )
            except Exception as finalize_exc:
                task_logger.exception(
                    "Failed to persist worker execution failure: {}", finalize_exc
                )
        finally:
            finished.set()
            await task_heartbeat
            _release_task_memory()

    active_tasks: set[asyncio.Task[None]] = set()
    idle_ticks = 0
    while not stop_event.is_set() or active_tasks:
        while not stop_event.is_set() and len(active_tasks) < MAX_CONCURRENT_TASKS:
            task_uuid = await _claim_next_task_uuid(instance_id)
            if not task_uuid:
                break

            idle_ticks = 0
            task = asyncio.create_task(_run_claimed_task(task_uuid), name=f"task-{task_uuid}")
            active_tasks.add(task)

        if active_tasks:
            done, pending = await asyncio.wait(
                active_tasks,
                timeout=POLL_INTERVAL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            active_tasks = set(pending)
            if done:
                idle_ticks = 0
            continue

        idle_ticks += 1
        if idle_ticks % max(1, IDLE_LOG_EVERY) == 0:
            logger.info("Task worker idle, waiting for queued tasks")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    stop_event.set()
    await recovery_sweep
    await asyncio.to_thread(runtime_guard.stop)
    await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
    task_manager.set_broadcast_hook(None)
    await runtime_registry.release_lease("worker", instance_id)
    await runtime_registry.remove_heartbeat("worker", instance_id)
    await runtime_registry.close()
    await control_plane_events.close()
    logger.info("Task worker stopped")
    if lease_lost.is_set():
        raise RuntimeError("Task worker stopped because its singleton lease was lost")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
