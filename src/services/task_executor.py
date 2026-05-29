"""
Task Executor — runs tasks in the background (API or CLI).

Picks up a Task by UUID, resolves the correct service, and executes it
with full task_lifecycle wrapping.  Progress updates flow through
task_manager → optional broadcast hook → WebSocket clients.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Optional

from sqlalchemy import select

from src.core import get_database, get_logger, init_app
from src.core.task_manager import task_manager
from src.domain import (
    AgentWorkflowRun,
    AgentWorkflowStep,
    Report,
    ReportSectionRun,
    ReportSectionRunStatus,
    ReportStatus,
    Task,
    TaskStatus,
    TaskType,
)
from src.services._lifecycle import task_lifecycle

logger = get_logger(__name__)

# Global set of currently-running task UUIDs (prevents double-execution)
_running: set[str] = set()


async def recover_interrupted_tasks_on_startup() -> int:
    """Mark orphaned RUNNING tasks as cancelled after a task worker restart."""
    message = (
        "Task worker restarted while this task was running. "
        "Resume the task to continue from the last checkpoint."
    )
    now = datetime.now(timezone.utc)
    recovered: list[tuple[str, int]] = []

    async with get_database() as db:
        tasks = (
            await db.execute(select(Task).where(Task.status == TaskStatus.RUNNING))
        ).scalars().all()

        for task in tasks:
            task.status = TaskStatus.CANCELLED
            task.completed_at = now
            if task.started_at:
                task.actual_duration = int((now - task.started_at).total_seconds())
            task.last_error = message

            report_id = task.report_id
            output_data = task.output_data if isinstance(task.output_data, dict) else {}
            if report_id is None and isinstance(output_data.get("report_id"), int):
                report_id = int(output_data["report_id"])
                task.report_id = report_id

            if report_id is not None:
                report = await db.get(Report, report_id)
                if report and report.status in [
                    ReportStatus.PENDING,
                    ReportStatus.GENERATING,
                    ReportStatus.REVIEWING,
                ]:
                    report.status = ReportStatus.FAILED
                    report.error_message = message

                pending_runs = (
                    await db.execute(
                        select(ReportSectionRun).where(
                            ReportSectionRun.report_id == report_id,
                            ReportSectionRun.status.in_([
                                ReportSectionRunStatus.QUEUED,
                                ReportSectionRunStatus.RUNNING,
                            ]),
                        )
                    )
                ).scalars().all()
                for run in pending_runs:
                    run.status = ReportSectionRunStatus.CANCELLED
                    run.error_message = message
                    run.ended_at = now

            if task.task_type == TaskType.AGENT_WORKFLOW:
                agent_run = (
                    await db.execute(
                        select(AgentWorkflowRun).where(AgentWorkflowRun.task_id == task.id)
                    )
                ).scalar_one_or_none()
                if agent_run is not None and agent_run.status not in {"completed", "failed", "cancelled"}:
                    agent_run.status = "cancelled"
                    agent_run.error_message = message
                    agent_run.ended_at = now
                    agent_run.metadata_ = {**(agent_run.metadata_ or {}), "recovered_after_restart": True}
                    pending_steps = (
                        await db.execute(
                            select(AgentWorkflowStep).where(
                                AgentWorkflowStep.run_id == agent_run.id,
                                AgentWorkflowStep.status.in_(["running", "pending"]),
                            )
                        )
                    ).scalars().all()
                    for step in pending_steps:
                        step.status = "cancelled"
                        step.error_message = message
                        step.ended_at = now

            recovered.append((task.task_uuid, task.progress or 0))

        await db.commit()

    for task_uuid, progress in recovered:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="warning",
            title="Task Recovered After Restart",
            content=message,
            content_type="text",
        )
        await task_manager._broadcast(
            {
                "event": "task_status",
                "task_uuid": task_uuid,
                "status": TaskStatus.CANCELLED.value,
                "progress": progress,
            }
        )

    if recovered:
        logger.warning(f"Recovered {len(recovered)} interrupted running task(s) after startup")

    return len(recovered)


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

    # RUNNING is accepted here because worker claim is now atomic and marks
    # the row as RUNNING before dispatch, preventing multi-worker double picks.
    if task.status not in (
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.RUNNING,
    ):
        raise ValueError(
            f"Task {task_uuid} has status '{task.status}' — "
            "only pending/queued/failed/cancelled/running(claimed) tasks can be executed"
        )

    # Resume semantics: re-executing a failed/cancelled report task should always
    # reuse partial output from prior attempts, regardless of stale input_data.
    if task.task_type == TaskType.GENERATE_REPORT and task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
        input_data = dict(task.input_data or {})
        changed = False
        if input_data.get("reuse_from_failed") is not True:
            input_data["reuse_from_failed"] = True
            changed = True
        if (input_data.get("reuse_strategy") or "auto").strip().lower() not in ("resume", "manual"):
            input_data["reuse_strategy"] = "resume"
            changed = True
        if changed:
            async with get_database() as db:
                task_obj = await db.get(Task, task.id)
                if task_obj:
                    task_obj.input_data = input_data
                    await db.commit()
            task.input_data = input_data
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="info",
                title="Resume Mode Enabled",
                content=(
                    "Auto-enabled reuse_from_failed=true and reuse_strategy=resume for "
                    "failed/cancelled task re-execution. Existing report sections will be reused "
                    "where possible."
                ),
                content_type="text",
            )

    if task.status == TaskStatus.CANCELLED or await task_manager.is_cancel_requested(task_uuid):
        await task_manager.clear_task_cancel_request(task_uuid)

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
    elif task_type == TaskType.UPDATE_DISEASE_KNOWLEDGE:
        return await _run_disease_knowledge(task)
    elif task_type == TaskType.EXPORT_DATA:
        return await _run_export(task)
    elif task_type == TaskType.AGENT_WORKFLOW:
        return await _run_agent_workflow(task)
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
    language = inp.get("language", "en")
    days = inp.get("days", 365)
    enable_review = inp.get("enable_review", True)
    send_email = inp.get("send_email", False)
    report_layout = inp.get("report_layout", "analytical_v3")
    analysis_depth = inp.get("analysis_depth", "deep")
    quality_threshold = float(inp.get("quality_threshold", 0.85))
    reuse_from_failed = inp.get("reuse_from_failed", True)
    reuse_strategy = inp.get("reuse_strategy", "auto")
    reuse_report_id = inp.get("reuse_report_id")

    report_id_ref = [None]
    async with task_lifecycle(task, report_id_ref=report_id_ref, exit_on_cancel=False):
        service = ReportService()
        result = await service.execute(
            task=task,
            country_code=country_code,
            report_type=report_type,
            period_start_iso=period_start,
            period_end_iso=period_end,
            language=language,
            days=days,
            enable_review=enable_review,
            send_email=send_email,
            report_layout=report_layout,
            analysis_depth=analysis_depth,
            quality_threshold=quality_threshold,
            reuse_from_failed=reuse_from_failed,
            reuse_strategy=reuse_strategy,
            reuse_report_id=reuse_report_id,
            report_id_ref=report_id_ref,
        )
        report_id_ref[0] = result.report_id

        output = {
            "report_id": result.report_id,
            "report_uuid": result.report_uuid,
            "status": result.status,
            "files": result.output_files,
            "sections_count": result.sections_count,
            "reused": result.reused,
        }
        if result.email_delivery is not None:
            output["email_delivery"] = result.email_delivery

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.report_id = result.report_id
                task_obj.output_data = output
                await db.commit()

        return output


async def _run_export(task: Task) -> Dict[str, Any]:
    """Execute an EXPORT_DATA task using the site release workflow."""
    from src.services.data_release_service import data_release_service

    async with task_lifecycle(task, exit_on_cancel=False):
        return await data_release_service.execute_release_task(task)


# ── Agent workflow handler ───────────────────────────────────────────────────

async def _run_agent_workflow(task: Task) -> Dict[str, Any]:
    """Execute an AGENT_WORKFLOW task using the generic workflow service."""
    from src.services.agent_workflow_service import agent_workflow_service

    async with task_lifecycle(task, exit_on_cancel=False):
        result = await agent_workflow_service.execute(task)

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()

        return result


# ── Disease knowledge handler ────────────────────────────────────────────────

async def _run_disease_knowledge(task: Task) -> Dict[str, Any]:
    """Execute an UPDATE_DISEASE_KNOWLEDGE task using the knowledge service."""
    from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService

    async with task_lifecycle(task, exit_on_cancel=False):
        service = DiseaseKnowledgeUpdateService()
        result = await service.execute_task(task)

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()

        return result
