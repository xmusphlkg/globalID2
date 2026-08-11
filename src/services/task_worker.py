"""Standalone worker process for executing queued tasks.

This worker decouples task execution from the API process. The API only marks tasks
as queued, and this worker continuously polls and executes them.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Optional

from sqlalchemy import case, select

from src.core import get_database, get_logger
from src.core.config import get_config
from src.domain import Task, TaskPriority, TaskStatus
from src.services.task_executor import execute_task, recover_interrupted_tasks_on_startup
from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.task_manager import task_manager

logger = get_logger(__name__)

TASK_WORKER_CONFIG = get_config().task_worker
POLL_INTERVAL_SECONDS = TASK_WORKER_CONFIG.poll_interval_seconds
IDLE_LOG_EVERY = TASK_WORKER_CONFIG.idle_log_every
MAX_CONCURRENT_TASKS = TASK_WORKER_CONFIG.concurrency


async def _claim_next_task_uuid() -> Optional[str]:
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

        task.status = TaskStatus.RUNNING
        task.last_error = None
        await db.commit()
        return task.task_uuid


async def run_worker() -> None:
    """Main worker loop."""
    logger.info(
        "Task worker started (poll_interval=%ss, concurrency=%s)",
        POLL_INTERVAL_SECONDS,
        MAX_CONCURRENT_TASKS,
    )

    instance_id = runtime_registry.new_instance_id("worker")
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)

    recovered = await recover_interrupted_tasks_on_startup()
    if recovered:
        logger.warning("Recovered %s interrupted task(s) on worker startup", recovered)

    stop_event = asyncio.Event()
    heartbeat = asyncio.create_task(
        runtime_registry.run_heartbeat(
            "worker",
            instance_id,
            stop_event,
            metadata={"concurrency": MAX_CONCURRENT_TASKS},
        ),
        name="control-plane-worker-heartbeat",
    )
    await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)

    def _request_stop() -> None:
        logger.info("Stop signal received, task worker shutting down soon")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Fallback for environments that do not support signal handlers.
            pass

    async def _run_claimed_task(task_uuid: str) -> None:
        task_logger = logger.bind(task_uuid=task_uuid, worker_instance_id=instance_id)
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

    active_tasks: set[asyncio.Task[None]] = set()
    idle_ticks = 0
    while not stop_event.is_set() or active_tasks:
        while not stop_event.is_set() and len(active_tasks) < MAX_CONCURRENT_TASKS:
            task_uuid = await _claim_next_task_uuid()
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
    await heartbeat
    await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
    task_manager.set_broadcast_hook(None)
    await runtime_registry.close()
    await control_plane_events.close()
    logger.info("Task worker stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
