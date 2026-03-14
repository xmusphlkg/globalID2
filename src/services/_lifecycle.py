"""
Task lifecycle context manager.

Handles SIGINT/SIGTERM signal registration, task-status updates on entry/exit,
and workbook error logging — eliminating boilerplate from every CLI command.
"""
import signal
import sys
from contextlib import asynccontextmanager
from typing import Optional

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import Task, TaskStatus

logger = get_logger(__name__)


@asynccontextmanager
async def task_lifecycle(task: Task, *, report_id_ref: Optional[list] = None):
    """
    Async context manager that wraps one command execution.

    On entry  → sets task status to RUNNING.
    On normal exit → sets task status to COMPLETED.
    On KeyboardInterrupt → sets CANCELLED, marks associated report CANCELLED
                           (if report_id_ref is provided), then sys.exit(130).
    On Exception → sets FAILED, logs error to workbook, re-raises.

    Args:
        task: The Task ORM object (must already be persisted).
        report_id_ref: Optional single-element list whose first item is the
                       report DB id.  Pass an empty list and update it inside
                       the ``async with`` block; the manager will read it on
                       failure to also mark the report as FAILED/CANCELLED.
    """
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)

    def _noop(signum, frame):
        # Raise KeyboardInterrupt so the except branch below fires
        raise KeyboardInterrupt()

    old_sigterm = signal.signal(signal.SIGTERM, _noop)

    try:
        yield
        await task_manager.update_task_status(task.task_uuid, TaskStatus.COMPLETED)

    except KeyboardInterrupt:
        logger.warning(f"Task {task.task_uuid} cancelled by user")
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="warning",
            title="Task Cancelled",
            content="Interrupted by user (Ctrl+C)",
            content_type="text",
        )
        await task_manager.update_task_status(
            task.task_uuid,
            TaskStatus.CANCELLED,
            error_message="Interrupted by user",
        )
        await _mark_report(report_id_ref, "cancelled", "Interrupted by user")
        sys.exit(130)

    except Exception as exc:
        logger.error(f"Task {task.task_uuid} failed: {exc}")
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="error",
            title="Task Failed",
            content=f"Error: {exc}",
            content_type="text",
        )
        await task_manager.update_task_status(
            task.task_uuid,
            TaskStatus.FAILED,
            error_message=str(exc),
        )
        await _mark_report(report_id_ref, "failed", str(exc))
        raise

    finally:
        signal.signal(signal.SIGTERM, old_sigterm)


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
                report.status = (
                    ReportStatus.CANCELLED if new_status == "cancelled" else ReportStatus.FAILED
                )
                report.error_message = error_message
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark report {report_id} as {new_status}: {e}")
