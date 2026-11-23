"""Bounded recovery of archived province reports after visual capacity returns.

Completed source tasks stay immutable. A recovery gets its own task/artifacts,
uses the same visual quorum and store gates, and leaves the regular cadence alone.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from src.core import get_database, get_logger
from src.domain import (
    AutomationJob,
    RunStage,
    ScheduledJobState,
    Task,
    TaskStatus,
    TaskType,
)
from src.services.cn_province_image_review import PROTOCOL, native_model_capacity
from src.services.crawl_task_service import crawl_task_service
from src.services.ingestion.artifacts import read_artifact, run_artifact_io

SOURCE = "cn_province_monthly_report"
RECOVERY_KEY = "province_image_recovery"
MAX_RECOVERIES = 2
BASE_DELAY_SECONDS = 300
logger = get_logger(__name__)


def capacity_blocked_panel(metadata: dict) -> int | None:
    """Recognize only untouched visual panels blocked by model preflight.

    Older audits already contain the per-seat reason, so recovery does not need
    a migration or a synthetic rewrite of their completion status.
    """
    province = metadata.get("province_ingestion") or {}
    audit = province.get("source_audit") or {}
    if (province.get("status") != "needs_review" or province.get("revision_candidates")
            or audit.get("revision_candidate_count") or audit.get("suspected_previous_month_copies")):
        return None
    for error in audit.get("errors") or []:
        if (error.get("stage") == "reconcile" or error.get("status") in {
                "image_capture_failed", "image_document_limit", "attachment_fetch_failed"}):
            return None
    pending = [item for item in audit.get("image_reviews") or [] if item.get("status") != "validated"]
    if not pending:
        return None
    panels = set()
    for item in pending:
        review = item.get("audit") or {}
        panel, quorum = review.get("panel_size"), review.get("quorum")
        available = review.get("available_native_models_at_start")
        readings = review.get("readings") or []
        if (review.get("protocol") != PROTOCOL or type(panel) is not int or panel not in {3, 5, 7}
                or quorum != panel // 2 + 1 or type(available) is not int or available >= quorum
                or not review.get("documents") or len(readings) != panel
                or any(slot.get("status") != "absent"
                       or slot.get("reason") != "insufficient_distinct_native_models"
                       or slot.get("model") or slot.get("raw_response") for slot in readings)):
            return None
        panels.add(panel)
    return next(iter(panels)) if len(panels) == 1 else None


def recovery_candidate(task, *, now: datetime) -> dict | None:
    """Return persisted recovery lineage only after its bounded delay."""
    if task.status != TaskStatus.COMPLETED or task.completed_at is None:
        return None
    inputs = task.input_data or {}
    if inputs.get("source") != SOURCE or not inputs.get("automation_job_id"):
        return None
    panel = capacity_blocked_panel(task.metadata_ or {})
    if panel is None:
        return None
    previous = inputs.get(RECOVERY_KEY) or {}
    attempt = previous.get("attempt", 0)
    if type(attempt) is not int or not 0 <= attempt < MAX_RECOVERIES:
        return None
    completed = task.completed_at
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    if now < completed + timedelta(seconds=BASE_DELAY_SECONDS * 2 ** attempt):
        return None
    return {"attempt": attempt + 1, "max_attempts": MAX_RECOVERIES,
            "root_task_uuid": previous.get("root_task_uuid") or task.task_uuid,
            "source_task_uuid": task.task_uuid, "panel_size": panel}


async def _latest_task(db, job_id):
    return (await db.execute(select(Task).where(
        Task.task_type == TaskType.CRAWL_DATA,
        Task.input_data["automation_job_id"].as_string() == job_id,
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(1))).scalar_one_or_none()


async def recover_pending_image_reviews(jobs, *, now: datetime | None = None) -> list[str]:
    """Enqueue capacity-only recoveries, serialized with pause and country work.

    Candidate reads are cheap and unlocked. Capacity is checked once per panel,
    then job/state locks and a fresh latest-task read reject stale candidates.
    No schedule timestamp is changed, including an operator's postponed run.
    """
    current = now or datetime.now(timezone.utc)
    candidates = []
    async with get_database() as db:
        for job in jobs:
            if not job.enabled or job.source != SOURCE:
                continue
            task = await _latest_task(db, job.job_id)
            plan = recovery_candidate(task, now=current) if task is not None else None
            if plan:
                candidates.append((job.job_id, task.task_uuid, plan["panel_size"]))
    capacities = {panel: await native_model_capacity(panel)
                  for panel in sorted({panel for _, _, panel in candidates})}
    queued = []
    for job_id, expected_task_uuid, panel in candidates:
        if not capacities[panel]["quorum_available"]:
            continue
        async with get_database() as db:
            job = (await db.execute(select(AutomationJob).where(
                AutomationJob.job_id == job_id,
            ).with_for_update(skip_locked=True))).scalar_one_or_none()
            if job is None or not job.enabled or job.source != SOURCE:
                continue
            state = (await db.execute(select(ScheduledJobState).where(
                ScheduledJobState.job_kind == "ingestion", ScheduledJobState.job_id == job_id,
            ).with_for_update())).scalar_one_or_none()
            # A NULL persisted schedule is paused, even if a cached config says enabled.
            if state is None or state.next_run_at is None:
                continue
            task = await _latest_task(db, job_id)
            plan = recovery_candidate(task, now=current) if task is not None else None
            if not plan or task.task_uuid != expected_task_uuid:
                continue
            stages = (await db.execute(select(RunStage).where(
                RunStage.task_uuid == task.task_uuid,
            ).order_by(RunStage.attempt.desc()))).scalars().all()
            if not stages:
                continue
            latest_attempt = max(stage.attempt for stage in stages)
            latest = {stage.stage: stage for stage in stages if stage.attempt == latest_attempt}
            if any(name not in latest or latest[name].status != "completed"
                   for name in ("collect", "organize")):
                continue
            plan["source_attempt"] = latest_attempt
            inputs = task.input_data or {}
            if inputs.get("country_code") != job.country_code:
                continue
            result = await crawl_task_service.enqueue_crawl_task(
                country_code=job.country_code, source=SOURCE, force=False,
                process=bool(job.process and inputs.get("process", True)), save_raw=True, fill_missing=False,
                include_current_month=False, revision_window_months=job.revision_window_months,
                priority=job.priority,
                description="Resume archived province image review after native model capacity recovered",
                metadata={"automation_job_id": job_id, "automation_job_name": job.name,
                          "scheduled_trigger": True, "manual_trigger": False,
                          "retry_threshold": job.retry_threshold, RECOVERY_KEY: plan},
            )
            if not result.created:
                continue
            # Only execution evidence changes; the weekly schedule remains intact.
            state.last_task_uuid = result.task.task_uuid
            state.last_status = "queued"
            state.last_started_at = current
            state.last_finished_at = current
            state.last_error = None
            await db.commit()
            queued.append(result.task.task_uuid)
            logger.info(f"Province image recovery queued job={job_id} task={result.task.task_uuid} "
                        f"source={task.task_uuid} attempt={plan['attempt']}/{MAX_RECOVERIES}")
    return queued


async def copy_recovery_artifacts(writer, *, recovery, country_code: str, source: str):
    """Re-register checked archived bytes for the new task without source HTTP."""
    from src.domain import SourceArtifact

    if not isinstance(recovery, dict) or source != SOURCE:
        raise ValueError("Invalid province image recovery scope")
    source_task_uuid = str(recovery.get("source_task_uuid") or "")
    UUID(source_task_uuid)
    attempt = recovery.get("source_attempt")
    recovery_number = recovery.get("attempt")
    if (source_task_uuid == writer.task_uuid or type(attempt) is not int or attempt < 1
            or type(recovery_number) is not int or not 1 <= recovery_number <= MAX_RECOVERIES):
        raise ValueError("Invalid province image recovery lineage")
    async with get_database() as db:
        task = (await db.execute(select(Task).where(
            Task.task_uuid == source_task_uuid,
        ))).scalar_one_or_none()
        if task is None or task.status != TaskStatus.COMPLETED or task.task_type != TaskType.CRAWL_DATA:
            raise ValueError("Province image recovery requires a completed source task")
        inputs = task.input_data or {}
        previous = inputs.get(RECOVERY_KEY) or {}
        panel = capacity_blocked_panel(task.metadata_ or {})
        if (inputs.get("country_code") != country_code or inputs.get("source") != source
                or panel is None or panel != recovery.get("panel_size")
                or recovery_number != previous.get("attempt", 0) + 1
                or recovery.get("root_task_uuid") != (previous.get("root_task_uuid") or task.task_uuid)):
            raise ValueError("Province image recovery source scope or audit mismatch")
        stages = (await db.execute(select(RunStage).where(
            RunStage.task_uuid == source_task_uuid,
        ).order_by(RunStage.attempt.desc()))).scalars().all()
        if not stages or max(stage.attempt for stage in stages) != attempt:
            raise ValueError("Province image recovery source attempt is not current")
        stages = {stage.stage: stage for stage in stages if stage.attempt == attempt}
        if any(name not in stages or stages[name].status != "completed"
               for name in ("collect", "organize")):
            raise ValueError("Province image recovery requires completed source stages")
        artifacts = (await db.execute(select(SourceArtifact).where(
            SourceArtifact.task_uuid == source_task_uuid, SourceArtifact.attempt == attempt,
        ).order_by(SourceArtifact.id.asc()))).scalars().all()
        if (len(artifacts) != (stages["collect"].metrics or {}).get("artifact_count")
                or any(artifact.kind not in {"province_plan", "province_response"} for artifact in artifacts)):
            raise ValueError("Province image recovery source artifacts are incomplete")
    plans = [artifact for artifact in artifacts if artifact.kind == "province_plan"]
    if len(plans) != 1:
        raise ValueError("Province image recovery requires one archived collection plan")
    plan = json.loads(await run_artifact_io(read_artifact, plans[0]))
    if plan.get("country_code") != country_code or plan.get("source") != source:
        raise ValueError("Province image recovery collection plan scope mismatch")
    for artifact in artifacts:
        # read_artifact verifies the immutable digest and size before new writes.
        payload = await run_artifact_io(read_artifact, artifact)
        await writer.write(payload, source_url=artifact.source_url, kind=artifact.kind,
                           metadata={**(artifact.metadata_ or {}), "image_recovery": {
                               "source_task_uuid": source_task_uuid, "source_attempt": attempt,
                               "source_sha256": artifact.sha256}})
    return writer.artifacts
