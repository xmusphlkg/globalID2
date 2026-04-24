"""
Task lifecycle context manager.

Handles SIGINT/SIGTERM signal registration, task-status updates on entry/exit,
and workbook error logging — eliminating boilerplate from every CLI command.
"""
import signal
import sys
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy import select

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import Task, TaskStatus, TaskType
from src.services.automation_service import automation_service
from src.services.data_release_service import data_release_service
from src.services.exceptions import TaskCancelledError
from src.services.task_alert_service import task_alert_service

logger = get_logger(__name__)


@asynccontextmanager
async def task_lifecycle(task: Task, *, report_id_ref: Optional[list] = None, exit_on_cancel: bool = True):
    """
    Async context manager that wraps one command execution.

    On entry  → sets task status to RUNNING.
    On normal exit → sets task status to COMPLETED.
    On KeyboardInterrupt → sets CANCELLED, marks associated report CANCELLED
                           (if report_id_ref is provided), then sys.exit(130)
                           when *exit_on_cancel* is True (CLI mode).
    On Exception → sets FAILED, logs error to workbook, re-raises.

    Args:
        task: The Task ORM object (must already be persisted).
        report_id_ref: Optional single-element list whose first item is the
                       report DB id.  Pass an empty list and update it inside
                       the ``async with`` block; the manager will read it on
                       failure to also mark the report as FAILED/CANCELLED.
        exit_on_cancel: If True (default/CLI), call sys.exit(130) on
                        KeyboardInterrupt.  Set False for API background tasks
                        to avoid killing the server process.
    """
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)

    # Only install signal handlers when running in CLI mode (main thread)
    import threading
    is_main_thread = threading.current_thread() is threading.main_thread()

    old_sigterm = None
    if is_main_thread and exit_on_cancel:
        def _noop(signum, frame):
            raise KeyboardInterrupt()
        old_sigterm = signal.signal(signal.SIGTERM, _noop)

    try:
        yield
        if await task_manager.is_cancel_requested(task.task_uuid):
            raise TaskCancelledError("Cancellation requested by user")
        await task_manager.update_task_status(task.task_uuid, TaskStatus.COMPLETED)
        try:
            await data_release_service.maybe_trigger_after_task_completion(task.task_uuid, task.task_type)
        except Exception as exc:
            logger.warning(f"Post-task data release auto-trigger skipped for {task.task_uuid}: {exc}")

    except KeyboardInterrupt:
        await _handle_task_cancelled(task, report_id_ref, "Interrupted by user (Ctrl+C)")
        if exit_on_cancel:
            sys.exit(130)

    except TaskCancelledError as exc:
        await _handle_task_cancelled(task, report_id_ref, str(exc))
        if exit_on_cancel:
            sys.exit(130)

    except Exception as exc:
        logger.error(f"Task {task.task_uuid} failed: {exc}")
        try:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="error",
                title="Task Failed",
                content=f"Error: {exc}",
                content_type="text",
            )
        except Exception as log_exc:
            logger.warning(f"Failed to write failure workbook entry for {task.task_uuid}: {log_exc}")
        await task_manager.update_task_status(
            task.task_uuid,
            TaskStatus.FAILED,
            error_message=str(exc),
        )
        try:
            await task_alert_service.send_task_alert(task.task_uuid, TaskStatus.FAILED)
        except Exception as notify_exc:
            logger.warning(f"Failure alert skipped for {task.task_uuid}: {notify_exc}")
        await _mark_report(report_id_ref, "failed", str(exc))
        await _mark_agent_workflow(task, "failed", str(exc))
        raise

    finally:
        if old_sigterm is not None:
            signal.signal(signal.SIGTERM, old_sigterm)


async def _handle_task_cancelled(task: Task, report_id_ref: Optional[list], message: str) -> None:
    logger.warning(f"Task {task.task_uuid} cancelled: {message}")
    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="warning",
            title="Task Cancelled",
            content=message,
            content_type="text",
        )
    except Exception as log_exc:
        logger.warning(f"Failed to write cancellation workbook entry for {task.task_uuid}: {log_exc}")
    await task_manager.update_task_status(
        task.task_uuid,
        TaskStatus.CANCELLED,
        error_message=message,
    )
    try:
        await task_alert_service.send_task_alert(task.task_uuid, TaskStatus.CANCELLED)
    except Exception as notify_exc:
        logger.warning(f"Cancellation alert skipped for {task.task_uuid}: {notify_exc}")
    await _mark_report(report_id_ref, "cancelled", message)
    await _mark_agent_workflow(task, "cancelled", message)


async def _mark_report(
    report_id_ref: Optional[list],
    new_status: str,
    error_message: str,
) -> None:
    """Update related Report status when a task fails or is cancelled."""
    if not report_id_ref or not report_id_ref[0]:
        return
    report_id = report_id_ref[0]
    try:
        from src.domain import Report, ReportStatus
        async with get_database() as db:
            report = await db.get(Report, report_id)
            if report:
                report.status = ReportStatus.FAILED
                report.error_message = error_message
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark report {report_id} as {new_status}: {e}")


async def _mark_agent_workflow(task: Task, new_status: str, error_message: str) -> None:
    """Update the generic agent workflow run that belongs to this task, if any."""
    if task.task_type != TaskType.AGENT_WORKFLOW:
        return
    try:
        from src.domain import AgentWorkflowRun

        async with get_database() as db:
            run = (await db.execute(select(AgentWorkflowRun).where(AgentWorkflowRun.task_id == task.id))).scalar_one_or_none()
            if run is None:
                return
            run.status = new_status
            run.error_message = error_message
            run.ended_at = datetime.now(timezone.utc)
            run.metadata_ = {**(run.metadata_ or {}), "task_lifecycle_status": new_status}
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark agent run for task {task.task_uuid} as {new_status}: {e}")
