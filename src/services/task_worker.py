"""Standalone worker process for executing queued tasks.

This worker decouples task execution from the API process. The API only marks tasks
as queued, and this worker continuously polls and executes them.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Optional

from sqlalchemy import case, select

from src.core import get_database, get_logger
from src.domain import Task, TaskPriority, TaskStatus
from src.services.task_executor import execute_task, recover_interrupted_tasks_on_startup

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = float(os.getenv("TASK_WORKER_POLL_INTERVAL", "2"))
IDLE_LOG_EVERY = int(os.getenv("TASK_WORKER_IDLE_LOG_EVERY", "30"))


async def _claim_next_task_uuid() -> Optional[str]:
    """Pick one runnable task and return its UUID.

    Status transition here is intentionally lightweight:
    - pending -> queued
    - queued  -> queued
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
            .limit(1)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None

        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.QUEUED

        return task.task_uuid


async def run_worker() -> None:
    """Main worker loop."""
    logger.info(
        "Task worker started (poll_interval=%ss)",
        POLL_INTERVAL_SECONDS,
    )

    recovered = await recover_interrupted_tasks_on_startup()
    if recovered:
        logger.warning("Recovered %s interrupted task(s) on worker startup", recovered)

    stop_event = asyncio.Event()

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

    idle_ticks = 0
    while not stop_event.is_set():
        task_uuid = await _claim_next_task_uuid()
        if not task_uuid:
            idle_ticks += 1
            if idle_ticks % max(1, IDLE_LOG_EVERY) == 0:
                logger.info("Task worker idle, waiting for queued tasks")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        idle_ticks = 0
        try:
            logger.info("Executing queued task %s", task_uuid)
            await execute_task(task_uuid)
        except Exception as exc:
            logger.exception("Worker failed executing task %s: %s", task_uuid, exc)

    logger.info("Task worker stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
