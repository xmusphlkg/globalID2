"""
Task Executor — runs tasks in the background (API or CLI).

Picks up a Task by UUID, resolves the correct service, and executes it
with full task_lifecycle wrapping.  Progress updates flow through
task_manager → optional broadcast hook → WebSocket clients.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Dict, Optional

from src.core import get_database, get_logger, init_app
from src.core.task_manager import task_manager
from src.domain import Task, TaskStatus, TaskType
from src.services._lifecycle import task_lifecycle

logger = get_logger(__name__)

# Global set of currently-running task UUIDs (prevents double-execution)
_running: set[str] = set()


async def execute_task(task_uuid: str) -> Dict[str, Any]:
    """
    Execute a task identified by *task_uuid*.

    Returns the task's ``output_data`` dict on success.
    Raises ``ValueError`` for unknown task types or invalid state,
    ``RuntimeError`` for execution failures.
    """
    if task_uuid in _running:
        raise RuntimeError(f"Task {task_uuid} is already running")

    # Load the task from DB
    task = await task_manager.get_task_by_uuid(task_uuid)
    if task is None:
        raise ValueError(f"Task not found: {task_uuid}")

    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.FAILED):
        raise ValueError(
            f"Task {task_uuid} has status '{task.status}' — "
            "only pending/queued/failed tasks can be executed"
        )

    _running.add(task_uuid)
    try:
        output = await _dispatch(task)
        return output
    finally:
        _running.discard(task_uuid)


async def execute_task_background(task_uuid: str) -> None:
    """Fire-and-forget wrapper around execute_task for asyncio.create_task()."""
    try:
        await execute_task(task_uuid)
    except Exception as exc:
        logger.error(f"Background task {task_uuid} failed: {exc}")


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def _dispatch(task: Task) -> Dict[str, Any]:
    """Route *task* to the correct service handler."""
    task_type = task.task_type
    if task_type == TaskType.CRAWL_DATA:
        return await _run_crawl(task)
    elif task_type == TaskType.GENERATE_REPORT:
        return await _run_report(task)
    else:
        raise ValueError(f"Unsupported task type for execution: {task_type}")


# ── Crawl handler ─────────────────────────────────────────────────────────────

async def _run_crawl(task: Task) -> Dict[str, Any]:
    """Execute a CRAWL_DATA task using CrawlService."""
    from src.services.crawl_service import CrawlService

    inp: dict = task.input_data or {}
    country_code = inp.get("country", inp.get("country_code", "CN")).upper()
    source = inp.get("source", "all")
    force = inp.get("force", False)
    process = inp.get("process", True)
    save_raw = inp.get("save_raw", True)
    fill_missing = inp.get("fill_missing", True)

    async with task_lifecycle(task, exit_on_cancel=False):
        service = CrawlService()
        result = await service.execute(
            task=task,
            country_code=country_code,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
        )

        output = {
            "new_reports": result.new_reports,
            "processed_reports": result.processed_reports,
            "total_records": result.total_records,
            "crawl_run_id": result.crawl_run_id,
        }

        # Persist output_data
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = output
                await db.commit()

        return output


# ── Report handler ────────────────────────────────────────────────────────────

async def _run_report(task: Task) -> Dict[str, Any]:
    """Execute a GENERATE_REPORT task using ReportService."""
    from src.services.report_service import ReportService

    inp: dict = task.input_data or {}
    country_code = inp.get("country", inp.get("country_code", "CN")).upper()
    report_type = inp.get("report_type", "monthly")
    period_start = inp.get("period_start")
    period_end = inp.get("period_end")
    days = inp.get("days", 365)
    enable_review = inp.get("enable_review", True)
    send_email = inp.get("send_email", False)

    report_id_ref = [None]
    async with task_lifecycle(task, report_id_ref=report_id_ref, exit_on_cancel=False):
        service = ReportService()
        result = await service.execute(
            task=task,
            country_code=country_code,
            report_type=report_type,
            period_start_iso=period_start,
            period_end_iso=period_end,
            days=days,
            enable_review=enable_review,
            send_email=send_email,
        )
        report_id_ref[0] = result.report_id

        output = {
            "report_id": result.report_id,
            "status": result.status,
            "files": result.output_files,
            "sections_count": result.sections_count,
            "reused": result.reused,
        }

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = output
                await db.commit()

        return output
