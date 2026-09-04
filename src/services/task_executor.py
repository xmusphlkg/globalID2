"""
Task Executor — runs tasks in the background (API or CLI).

Picks up a Task by UUID, resolves the correct service, and executes it
with full task_lifecycle wrapping.  Progress updates flow through
task_manager → optional broadcast hook → WebSocket clients.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, Optional

from sqlalchemy import select

from src.core import get_database, get_logger, init_app
from src.core.task_manager import task_manager
from src.domain import (
    AgentWorkflowRun,
    AgentWorkflowStep,
    LiteratureIngestRun,
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

RECOVERABLE_IDEMPOTENT_TASK_TYPES = frozenset(
    {
        TaskType.SYNC_LITERATURE,
        TaskType.ENRICH_LITERATURE,
        TaskType.DISCOVER_LITERATURE_GAPS,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
    }
)


def _as_aware_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _task_heartbeat_at(task: Task) -> datetime | None:
    metadata = task.metadata_ if isinstance(getattr(task, "metadata_", None), dict) else {}
    lease = metadata.get("task_lease") if isinstance(metadata.get("task_lease"), dict) else {}
    return (
        _as_aware_utc(lease.get("heartbeat_at"))
        or _as_aware_utc(lease.get("claimed_at"))
        or _as_aware_utc(getattr(task, "updated_at", None))
        or _as_aware_utc(getattr(task, "started_at", None))
        or _as_aware_utc(getattr(task, "created_at", None))
    )


async def _fail_interrupted_literature_ingest_runs(
    db: Any,
    *,
    task_uuid: str,
    now: datetime,
) -> int:
    """Terminalize only running ingest attempts bound to one recovered task.

    The exact task UUID is the ownership boundary.  A replacement attempt has
    a different task UUID (or creates its own run after this transaction), so
    time proximity and worker identity are deliberately not used here.
    """
    runs = (
        await db.execute(
            select(LiteratureIngestRun)
            .where(
                LiteratureIngestRun.task_uuid == task_uuid,
                LiteratureIngestRun.status == "running",
            )
            .with_for_update()
        )
    ).scalars().all()
    for run in runs:
        checkpoint = dict(run.checkpoint or {})
        checkpoint["task_uuid"] = task_uuid
        checkpoint["recovery"] = {
            "reason_code": "task_worker_lease_expired",
            "reconciled_at": now.isoformat(),
        }
        run.checkpoint = checkpoint
        run.status = "failed"
        run.completed_at = now
        run.error = "task_worker_lease_expired"
    return len(runs)


async def recover_interrupted_tasks_on_startup(
    *,
    stale_after_seconds: int = 180,
    now: datetime | None = None,
    exclude_owner: str | None = None,
    only_owner: str | None = None,
) -> int:
    """Recover only expired RUNNING tasks.

    Idempotent Research Radar tasks are requeued up to their persisted retry
    limit. Other task types retain the conservative cancellation semantics
    because automatically replaying them may duplicate external side effects.
    """
    message = (
        "Task worker lease expired while this task was running. "
        "The previous worker is no longer reporting heartbeats."
    )
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=stale_after_seconds)
    recovered: list[tuple[str, int, TaskStatus, str]] = []
    recovered_crawls: list[tuple[str, str, datetime]] = []

    async with get_database() as db:
        tasks = (
            await db.execute(
                select(Task)
                .where(Task.status == TaskStatus.RUNNING)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        for task in tasks:
            current_metadata = (
                task.metadata_
                if isinstance(getattr(task, "metadata_", None), dict)
                else {}
            )
            current_lease = (
                current_metadata.get("task_lease")
                if isinstance(current_metadata.get("task_lease"), dict)
                else {}
            )
            if exclude_owner and current_lease.get("owner") == exclude_owner:
                continue
            if only_owner and current_lease.get("owner") != only_owner:
                continue
            heartbeat_at = _task_heartbeat_at(task)
            if heartbeat_at is None or heartbeat_at > stale_cutoff:
                continue

            metadata = dict(current_metadata)
            lease = dict(metadata.get("task_lease") or {})
            lease["expired_at"] = now.isoformat()
            lease["released_at"] = now.isoformat()
            metadata["task_lease"] = lease
            history = list(metadata.get("task_recovery_history") or [])
            history.append(
                {
                    "at": now.isoformat(),
                    "previous_owner": lease.get("owner"),
                    "last_heartbeat_at": heartbeat_at.isoformat(),
                }
            )
            metadata["task_recovery_history"] = history[-10:]
            task.metadata_ = metadata

            cancellation_requested = bool(metadata.get("cancel_requested"))
            if cancellation_requested:
                cancellation_reason = str(
                    metadata.get("cancel_reason") or "Cancellation requested before worker recovery."
                )
                task.status = TaskStatus.CANCELLED
                task.completed_at = now
                if task.started_at:
                    started_at = task.started_at
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                    task.actual_duration = int((now - started_at).total_seconds())
                task.last_error = cancellation_reason
                lease["terminal_status"] = TaskStatus.CANCELLED.value
                task.metadata_ = metadata
                recovered.append(
                    (task.task_uuid, task.progress or 0, TaskStatus.CANCELLED, cancellation_reason)
                )
                continue

            if task.task_type == TaskType.SYNC_LITERATURE:
                await _fail_interrupted_literature_ingest_runs(
                    db,
                    task_uuid=task.task_uuid,
                    now=now,
                )

            retry_count = int(getattr(task, "retry_count", 0) or 0)
            max_retries = int(getattr(task, "max_retries", 3) or 0)
            if task.task_type in RECOVERABLE_IDEMPOTENT_TASK_TYPES and retry_count < max_retries:
                task.status = TaskStatus.QUEUED
                task.retry_count = retry_count + 1
                task.started_at = None
                task.completed_at = None
                task.actual_duration = None
                task.last_error = (
                    f"{message} Automatically requeued idempotent task "
                    f"(recovery {task.retry_count}/{max_retries})."
                )
                recovered.append(
                    (task.task_uuid, task.progress or 0, TaskStatus.QUEUED, task.last_error)
                )
                continue

            if task.task_type == TaskType.CRAWL_DATA:
                crawl_input = dict(task.input_data or {})
                crawl_country = str(
                    crawl_input.get("country")
                    or crawl_input.get("country_code")
                    or ""
                ).strip()
                if crawl_country:
                    crawl_started = task.started_at or (now - timedelta(minutes=5))
                    recovered_crawls.append(
                        (
                            crawl_country,
                            str(crawl_input.get("source") or "all"),
                            crawl_started - timedelta(minutes=1),
                        )
                    )
            task.status = TaskStatus.CANCELLED
            task.completed_at = now
            if task.started_at:
                started_at = task.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                task.actual_duration = int((now - started_at).total_seconds())
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

            terminal_status = (
                TaskStatus.FAILED
                if task.task_type in RECOVERABLE_IDEMPOTENT_TASK_TYPES
                else TaskStatus.CANCELLED
            )
            task.status = terminal_status
            if terminal_status == TaskStatus.FAILED:
                task.last_error = (
                    f"{message} Automatic recovery limit exhausted "
                    f"({retry_count}/{max_retries})."
                )
            recovered.append(
                (task.task_uuid, task.progress or 0, terminal_status, task.last_error)
            )

        await db.commit()

    if recovered_crawls:
        from src.services.crawl_service import CrawlService

        crawl_service = CrawlService()
        for country_code, source, started_after in recovered_crawls:
            try:
                await crawl_service.fail_current_run(
                    country_code=country_code,
                    source=source,
                    started_after=started_after,
                    error=RuntimeError(message),
                    status="cancelled",
                )
            except Exception as exc:
                logger.warning(
                    "Failed to finalize interrupted CrawlRun country={} source={}: {}",
                    country_code,
                    source,
                    exc,
                )

    for task_uuid, progress, status, recovery_message in recovered:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="warning",
            title=(
                "Stale Task Automatically Requeued"
                if status == TaskStatus.QUEUED
                else "Stale Task Recovered"
            ),
            content=recovery_message,
            content_type="text",
        )
        await task_manager._broadcast(
            {
                "event": "task_status",
                "task_uuid": task_uuid,
                "status": status.value,
                "progress": progress,
            }
        )

    if recovered:
        logger.warning(f"Recovered {len(recovered)} stale running task(s)")

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
        # A worker may claim a task immediately before a user or automated
        # catalogue rule requests cancellation.  Cancellation is terminal
        # until an explicit Operations retry clears the marker and requeues it.
        if task.status == TaskStatus.RUNNING:
            await task_manager.update_task_status(
                task_uuid,
                TaskStatus.CANCELLED,
                error_message="Cancellation requested before task execution began.",
            )
        return {"task_uuid": task_uuid, "cancelled": True}

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
    elif task_type == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES:
        return await _run_disease_knowledge_sources(task)
    elif task_type == TaskType.EXPORT_DATA:
        return await _run_export(task)
    elif task_type == TaskType.AGENT_WORKFLOW:
        return await _run_agent_workflow(task)
    elif task_type == TaskType.SYNC_SITUATION_HISTORY:
        return await _run_situation_history_sync(task)
    elif task_type == TaskType.REFRESH_SITUATION_SOURCES:
        return await _run_situation_sources_refresh(task)
    elif task_type == TaskType.SYNC_LITERATURE:
        return await _run_literature_sync(task)
    elif task_type == TaskType.ENRICH_LITERATURE:
        return await _run_literature_enrichment(task)
    elif task_type == TaskType.DISCOVER_LITERATURE_GAPS:
        return await _run_literature_gap_discovery(task)
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
    include_current_month = inp.get("include_current_month")
    revision_window_months = inp.get("revision_window_months")

    async with task_lifecycle(task, exit_on_cancel=False):
        service = CrawlService()
        execution_started = datetime.now(timezone.utc)
        try:
            result = await service.execute(
                task=task,
                country_code=country_code,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                include_current_month=include_current_month,
                revision_window_months=revision_window_months,
            )
        except Exception as exc:
            try:
                await service.fail_current_run(
                    country_code=country_code,
                    source=source,
                    started_after=execution_started,
                    error=exc,
                )
            except Exception as audit_exc:
                logger.warning(
                    "Failed to finalize CrawlRun for task {}: {}",
                    task.task_uuid,
                    audit_exc,
                )
            raise

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
    language = "zh"
    days = inp.get("days", 365)
    enable_review = inp.get("enable_review", True)
    send_email = inp.get("send_email", False)
    report_layout = "report_v4"
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


async def _run_situation_history_sync(task: Task) -> Dict[str, Any]:
    """Reconcile primary Situation snapshots into the history database."""
    from src.services.situation_history_service import sync_history

    async with task_lifecycle(task, exit_on_cancel=False):
        await task_manager.update_task_progress(task.task_uuid, 10)
        result = await sync_history(mode=str((task.input_data or {}).get("mode") or "reconcile"))
        await task_manager.update_task_progress(task.task_uuid, 100)
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()
        return result


async def _run_situation_sources_refresh(task: Task) -> Dict[str, Any]:
    """Fetch configured Situation adapters and publish a v3 analysis run."""
    from src.services.situation_v3.pipeline import refresh_situation_v3

    async with task_lifecycle(task, exit_on_cancel=False):
        fetch_events = bool((task.input_data or {}).get("fetch_events", True))
        await task_manager.update_task_progress(task.task_uuid, 10)
        refresh = await refresh_situation_v3(fetch_events=fetch_events)
        payload = refresh["report"]
        report = payload.get("report") or {}
        await task_manager.update_task_progress(task.task_uuid, 100)
        result = {
            "schema_version": payload.get("schema_version"),
            "report_id": report.get("report_id"),
            "run_id": refresh.get("run_id"),
            "checked_at": report.get("as_of"),
            "data_through": (payload.get("data_currency") or {}).get("latest_data_through"),
            "revision": report.get("revision"),
            "quality_gate_status": (payload.get("quality_gate") or {}).get("status"),
            "source_health": payload.get("sources") or [],
            "coverage": payload.get("coverage") or {},
            "timings": refresh.get("timings") or {},
            "fetch_events": fetch_events,
        }
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()
        return result


async def _run_literature_sync(task: Task) -> Dict[str, Any]:
    """Incrementally update the Research Radar literature catalogue."""
    from src.services.literature_service import literature_service

    async with task_lifecycle(task, exit_on_cancel=False):
        result = await literature_service.execute_task(task)
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()
        return result


async def _run_literature_enrichment(task: Task) -> Dict[str, Any]:
    """Generate review-only Research Radar evidence drafts via the model center."""
    from src.services.literature_service import literature_service

    async with task_lifecycle(task, exit_on_cancel=False):
        result = await literature_service.execute_enrichment_task(task)
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()
        return result


async def _run_literature_gap_discovery(task: Task) -> Dict[str, Any]:
    """Discover review-only literature candidates for active evidence gaps."""
    from src.services.literature_service import literature_service

    async with task_lifecycle(task, exit_on_cancel=False):
        result = await literature_service.execute_gap_discovery_task(task)
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()
        return result


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


async def _run_disease_knowledge_sources(task: Task) -> Dict[str, Any]:
    """Execute a source-only disease knowledge refresh task."""
    from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService

    async with task_lifecycle(task, exit_on_cancel=False):
        service = DiseaseKnowledgeUpdateService()
        result = await service.execute_source_refresh_task(task)

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = result
                await db.commit()

        return result
