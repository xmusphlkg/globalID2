"""Data release router for generated site publishing workflow."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.release import (
    DataReleaseChecksOut,
    DataReleaseConfigOut,
    DataReleaseJobCreate,
    DataReleaseJobOut,
    DataReleaseJobUpdate,
    DataReleaseTriggerResult,
)
from src.domain import DataReleaseJob
from src.services.data_release_service import data_release_service

router = APIRouter()


@router.get("/release", response_model=DataReleaseConfigOut)
async def get_data_release_config():
    return DataReleaseConfigOut(**(await data_release_service.snapshot_async()))


@router.get("/release/jobs", response_model=List[DataReleaseJobOut])
async def list_data_release_jobs():
    await data_release_service.ensure_storage()
    snapshot = await data_release_service.snapshot_async()
    return [DataReleaseJobOut(**item) for item in snapshot["jobs"]]


@router.post("/release/jobs", response_model=DataReleaseJobOut, status_code=201)
async def create_data_release_job(body: DataReleaseJobCreate, db: AsyncSession = Depends(get_db)):
    await data_release_service.ensure_storage()
    _validate_release_schedule(body.interval_minutes, body.daily_time)
    existing = (
        await db.execute(select(DataReleaseJob).where(DataReleaseJob.job_id == body.job_id.strip()))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Data release job already exists: {body.job_id}")

    job = DataReleaseJob(
        job_id=body.job_id.strip(),
        name=body.name.strip(),
        enabled=body.enabled,
        priority=(body.priority or "high").strip().lower(),
        auto_after_crawls=body.auto_after_crawls,
        include_git_push=body.include_git_push,
        include_cloudflare_deploy=body.include_cloudflare_deploy,
        require_clean_worktree=body.require_clean_worktree,
        interval_minutes=body.interval_minutes,
        daily_time=(body.daily_time.strip() if body.daily_time else None),
        timezone=(body.timezone.strip() if body.timezone else None),
        github_remote=(body.github_remote or "origin").strip(),
        github_branch=(body.github_branch.strip() if body.github_branch else None),
        cloudflare_project_name=(body.cloudflare_project_name.strip() if body.cloudflare_project_name else None),
        commit_message_template=(body.commit_message_template or "").strip()
        or "chore(data-release): publish site data {timestamp}",
        notes=(body.notes.strip() if body.notes else None),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    snapshot = await data_release_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return DataReleaseJobOut(**state_by_job[job.job_id])


@router.put("/release/jobs/{job_id}", response_model=DataReleaseJobOut)
async def update_data_release_job(job_id: str, body: DataReleaseJobUpdate, db: AsyncSession = Depends(get_db)):
    await data_release_service.ensure_storage()
    job = (
        await db.execute(select(DataReleaseJob).where(DataReleaseJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Data release job not found: {job_id}")

    updates = body.model_dump(exclude_unset=True)
    next_interval = updates["interval_minutes"] if "interval_minutes" in updates else job.interval_minutes
    next_daily = updates["daily_time"] if "daily_time" in updates else job.daily_time
    _validate_release_schedule(next_interval, next_daily)

    for field, value in updates.items():
        if isinstance(value, str):
            value = value.strip()
        if field == "priority" and isinstance(value, str):
            value = value.lower() or "high"
        if field in {"daily_time", "timezone", "github_branch", "cloudflare_project_name", "notes"}:
            value = value or None
        if field == "commit_message_template":
            value = value or "chore(data-release): publish site data {timestamp}"
        setattr(job, field, value)

    await db.commit()
    await db.refresh(job)
    snapshot = await data_release_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return DataReleaseJobOut(**state_by_job[job.job_id])


@router.delete("/release/jobs/{job_id}")
async def delete_data_release_job(job_id: str, db: AsyncSession = Depends(get_db)):
    await data_release_service.ensure_storage()
    job = (
        await db.execute(select(DataReleaseJob).where(DataReleaseJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Data release job not found: {job_id}")
    await db.delete(job)
    await db.commit()
    return {"ok": True, "job_id": job_id}


@router.post("/release/jobs/{job_id}/run", response_model=DataReleaseTriggerResult, status_code=202)
async def run_data_release_job(job_id: str):
    try:
        result = await data_release_service.trigger_job(job_id, manual=True, trigger="manual")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataReleaseTriggerResult(**result)


@router.get("/release/jobs/{job_id}/checks", response_model=DataReleaseChecksOut)
async def get_data_release_checks(job_id: str):
    try:
        result = await data_release_service.integration_checks(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataReleaseChecksOut(
        **result,
        raw=result,
    )


def _validate_release_schedule(interval_minutes: int | None, daily_time: str | None) -> None:
    if interval_minutes is None and not daily_time:
        return
    if interval_minutes is not None and interval_minutes <= 0:
        raise HTTPException(400, "interval_minutes must be greater than 0")
    if daily_time:
        parts = daily_time.split(":")
        if len(parts) != 2:
            raise HTTPException(400, "daily_time must be in HH:MM format")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise HTTPException(400, "daily_time must be numeric HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise HTTPException(400, "daily_time must be a valid HH:MM value")


def _job_out(job: DataReleaseJob, state: dict | None) -> DataReleaseJobOut:
    state = state or {}
    return DataReleaseJobOut(
        job_id=job.job_id,
        name=job.name,
        enabled=job.enabled,
        priority=job.priority,
        auto_after_crawls=job.auto_after_crawls,
        include_git_push=job.include_git_push,
        include_cloudflare_deploy=job.include_cloudflare_deploy,
        require_clean_worktree=job.require_clean_worktree,
        interval_minutes=job.interval_minutes,
        daily_time=job.daily_time,
        timezone=job.timezone,
        github_remote=job.github_remote,
        github_branch=job.github_branch,
        cloudflare_project_name=job.cloudflare_project_name,
        commit_message_template=job.commit_message_template,
        notes=job.notes,
        next_run_at=state.get("next_run_at"),
        last_started_at=state.get("last_started_at"),
        last_finished_at=state.get("last_finished_at"),
        last_status=state.get("last_status", "idle"),
        last_error=state.get("last_error"),
        last_task_uuid=state.get("last_task_uuid"),
        run_count=state.get("run_count", 0),
        skipped_count=state.get("skipped_count", 0),
    )
