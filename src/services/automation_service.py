"""Env-driven automation scheduler and task failure notifications."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import html
import json
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.core import get_config, get_database, get_logger
from src.core.source_scopes import canonicalize_task_source
from src.core.task_manager import task_manager
from src.domain import AutomationJob, Country, Task, TaskStatus, TaskType, TaskWorkbook
from src.services.crawl_task_service import crawl_task_service
from src.services.ingestion_resilience import (
    automatic_ingestion_trigger_eligible,
    classify_ingestion_failure,
)
from src.services.smtp_email_service import smtp_email_service
from src.services.settings_service import system_settings_service
from src.control_plane.schedule_state import schedule_state_repository

logger = get_logger(__name__)

AUTOMATION_COUNTRY_CODE_LENGTH = 10


def _country_code_resize_sql(
    dialect_name: str, current_length: int | None
) -> str | None:
    """Return the safe in-place widening statement for supported SQL dialects."""

    if current_length is None or current_length >= AUTOMATION_COUNTRY_CODE_LENGTH:
        return None
    if dialect_name == "postgresql":
        return (
            "ALTER TABLE automation_jobs ALTER COLUMN country_code "
            f"TYPE VARCHAR({AUTOMATION_COUNTRY_CODE_LENGTH})"
        )
    if dialect_name in {"mysql", "mariadb"}:
        return (
            "ALTER TABLE automation_jobs MODIFY country_code "
            f"VARCHAR({AUTOMATION_COUNTRY_CODE_LENGTH}) NOT NULL"
        )
    # SQLite does not enforce VARCHAR lengths, so existing VARCHAR(2) columns
    # already accept ISO 3166-2 values such as CA-ON.
    return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value).lower()


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


def _ingestion_job_health(
    job: "AutomationJobConfig",
    latest_task: Optional[Task],
    latest_success: Optional[Task],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return an operator-independent health verdict for one source job."""

    current = _as_aware_utc(now) or datetime.now(ZoneInfo("UTC"))
    success_at = _as_aware_utc(
        latest_success.completed_at if latest_success is not None else None
    )
    expected_minutes = max(1, int(job.interval_minutes or 1440))
    stale_after_minutes = expected_minutes * 2
    age_minutes = (
        max(0, int((current - success_at).total_seconds() // 60))
        if success_at is not None
        else None
    )

    if not job.enabled:
        status, reason = "disabled", "job is disabled"
    elif latest_task is None:
        status, reason = "never_run", "no scheduler-created task has been observed"
    else:
        task_status = _enum_value(latest_task.status)
        if task_status == TaskStatus.RETRYING.value:
            status, reason = "recovering", "a bounded automatic retry is scheduled"
        elif task_status in {TaskStatus.PENDING.value, TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}:
            status, reason = "active", f"latest task is {task_status}"
        elif task_status in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            status, reason = "failed", f"latest task is {task_status}"
        elif success_at is None:
            status, reason = "unknown", "latest task has no completed success timestamp"
        elif age_minutes is not None and age_minutes > stale_after_minutes:
            status, reason = (
                "stale",
                f"last success is {age_minutes} minutes old; limit is {stale_after_minutes}",
            )
        else:
            status, reason = "healthy", "latest scheduled task completed within cadence"

    return {
        "health_status": status,
        "health_reason": reason,
        "last_success_at": _iso(success_at),
        "last_success_age_minutes": age_minutes,
        "stale_after_minutes": stale_after_minutes,
    }


@dataclass
class AutomationJobConfig:
    job_id: str
    name: str
    country_code: str
    source: str = "all"
    enabled: bool = True
    priority: str = "normal"
    process: bool = True
    save_raw: bool = True
    fill_missing: bool = False
    force: bool = False
    include_current_month: bool = False
    revision_window_months: int = 3
    retry_threshold: int = 3
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_tz: str, default_retry_threshold: int) -> "AutomationJobConfig":
        job_id = str(payload.get("job_id") or payload.get("id") or "").strip()
        country_code = str(payload.get("country_code") or "").strip().upper()
        if not job_id:
            raise ValueError("automation job is missing job_id")
        if not country_code:
            raise ValueError(f"automation job '{job_id}' is missing country_code")
        return cls(
            job_id=job_id,
            name=str(payload.get("name") or job_id).strip(),
            country_code=country_code,
            source=canonicalize_task_source(
                str(payload.get("source") or "all").strip().lower(),
                country_code=country_code,
            ),
            enabled=bool(payload.get("enabled", True)),
            priority=str(payload.get("priority") or "normal").strip().lower(),
            process=bool(payload.get("process", True)),
            save_raw=bool(payload.get("save_raw", True)),
            fill_missing=bool(payload.get("fill_missing", False)),
            force=bool(payload.get("force", False)),
            include_current_month=bool(payload.get("include_current_month", False)),
            revision_window_months=max(
                1, min(24, int(payload.get("revision_window_months") or 3))
            ),
            retry_threshold=int(payload.get("retry_threshold") or default_retry_threshold),
            interval_minutes=(
                int(payload["interval_minutes"])
                if payload.get("interval_minutes") is not None
                else None
            ),
            daily_time=(
                str(payload.get("daily_time")).strip()
                if payload.get("daily_time")
                else None
            ),
            timezone=str(payload.get("timezone") or default_tz).strip(),
        )


@dataclass
class AutomationJobState:
    next_run_at: Optional[datetime] = None
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_status: str = "idle"
    last_error: Optional[str] = None
    last_task_uuid: Optional[str] = None
    run_count: int = 0
    skipped_count: int = 0


class AutomationService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._lock = asyncio.Lock()
        self._storage_lock = asyncio.Lock()
        self._storage_ready = False
        self._states: dict[str, AutomationJobState] = {}
        self._last_tick_at: Optional[datetime] = None

    def _config(self):
        return get_config().automation

    def _jobs(self) -> list[AutomationJobConfig]:
        raise RuntimeError("Use load_jobs() for async automation job access")

    async def ensure_storage(self) -> None:
        """Verify managed storage and seed configuration without running DDL.

        Schema changes are owned by Alembic. This guard deliberately fails fast
        when the deployment preflight/upgrade step has not been run.
        """
        if self._storage_ready:
            return
        async with self._storage_lock:
            if self._storage_ready:
                return
            try:
                async with get_database() as db:
                    await db.execute(select(AutomationJob.id).limit(1))
                await self._seed_jobs_from_env_if_needed()
            except Exception as exc:
                raise RuntimeError(
                    "automation_jobs storage is not ready; run the Alembic preflight and upgrade"
                ) from exc
            self._storage_ready = True

    async def _seed_jobs_from_env_if_needed(self) -> None:
        cfg = self._config()
        if not cfg.jobs:
            return
        async with get_database() as db:
            existing = (
                await db.execute(select(AutomationJob.id).limit(1))
            ).scalar_one_or_none()
            if existing is not None:
                return

            for raw_job in cfg.jobs:
                try:
                    job = AutomationJobConfig.from_dict(
                        raw_job,
                        default_tz=cfg.timezone,
                        default_retry_threshold=cfg.default_retry_threshold,
                    )
                except Exception as exc:
                    logger.warning("Ignoring invalid automation seed job {}: {}", raw_job, exc)
                    continue
                db.add(
                    AutomationJob(
                        job_id=job.job_id,
                        name=job.name,
                        country_code=job.country_code,
                        source=job.source,
                        enabled=job.enabled,
                        priority=job.priority,
                        process=job.process,
                        save_raw=job.save_raw,
                        fill_missing=job.fill_missing,
                        force=job.force,
                        include_current_month=job.include_current_month,
                        revision_window_months=job.revision_window_months,
                        retry_threshold=job.retry_threshold,
                        interval_minutes=job.interval_minutes,
                        daily_time=job.daily_time,
                        timezone=job.timezone,
                    )
                )
            await db.commit()

    async def load_jobs(self) -> list[AutomationJobConfig]:
        cfg = self._config()
        async with get_database() as db:
            rows = (
                await db.execute(
                    select(AutomationJob).order_by(AutomationJob.country_code.asc(), AutomationJob.job_id.asc())
                )
            ).scalars().all()

        jobs: list[AutomationJobConfig] = []
        for row in rows:
            jobs.append(
                AutomationJobConfig(
                    job_id=row.job_id,
                    name=row.name,
                    country_code=row.country_code,
                    source=canonicalize_task_source(
                        row.source,
                        country_code=row.country_code,
                    ),
                    enabled=row.enabled,
                    priority=row.priority,
                    process=row.process,
                    save_raw=row.save_raw,
                    fill_missing=row.fill_missing,
                    force=row.force,
                    include_current_month=row.include_current_month,
                    revision_window_months=row.revision_window_months,
                    retry_threshold=row.retry_threshold,
                    interval_minutes=row.interval_minutes,
                    daily_time=row.daily_time,
                    timezone=row.timezone or cfg.timezone,
                )
            )
        return jobs

    def _sync_state_schedule(
        self,
        job: AutomationJobConfig,
        state: AutomationJobState,
        *,
        reset: bool,
    ) -> None:
        if not job.enabled:
            state.next_run_at = None
            return
        if reset or state.next_run_at is None:
            tz_name = job.timezone or self._config().timezone
            now = datetime.now(ZoneInfo(tz_name))
            state.next_run_at = self._compute_next_run(job, now=now)

    async def reschedule_job(self, job_id: str) -> None:
        async with self._lock:
            job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
            if job is None:
                self._states.pop(job_id, None)
                return
            state = self._states.setdefault(job.job_id, AutomationJobState())
            self._sync_state_schedule(job, state, reset=True)
            await schedule_state_repository.save("ingestion", job.job_id, state)

    async def remove_job_state(self, job_id: str) -> None:
        async with self._lock:
            self._states.pop(job_id, None)
            await schedule_state_repository.remove("ingestion", job_id)

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.enabled:
            logger.info("Automation scheduler disabled by configuration")
            return
        await self.ensure_storage()
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        persisted = await schedule_state_repository.load("ingestion")
        for job in await self.load_jobs():
            state = self._states.setdefault(job.job_id, AutomationJobState())
            for field, value in persisted.get(job.job_id, {}).items():
                setattr(state, field, value)
            self._sync_state_schedule(job, state, reset=state.next_run_at is None)
            await schedule_state_repository.save("ingestion", job.job_id, state)
        loaded_jobs = await self.load_jobs()
        self._task = asyncio.create_task(self._run_loop(), name="globalid-automation-scheduler")
        logger.info("Automation scheduler started with {} configured job(s)", len(loaded_jobs))

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    async def _run_loop(self) -> None:
        cfg = self._config()
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            self._last_tick_at = datetime.now(ZoneInfo("UTC"))
            try:
                await self.requeue_due_automatic_retries()
                for job in await self.load_jobs():
                    if not job.enabled:
                        continue
                    state = self._states.setdefault(job.job_id, AutomationJobState())
                    now = datetime.now(ZoneInfo(job.timezone or cfg.timezone))
                    self._sync_state_schedule(job, state, reset=state.next_run_at is None)
                    if state.next_run_at and now >= state.next_run_at:
                        await self.trigger_job(job.job_id, manual=False)
            except Exception as exc:
                logger.exception("Automation scheduler tick failed: {}", exc)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=cfg.poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    def _automatic_retry_delays(self) -> tuple[int, int]:
        cfg = self._config()
        base_delay = max(
            5,
            int(getattr(cfg, "auto_retry_base_delay_seconds", 300) or 300),
        )
        max_delay = max(
            base_delay,
            int(getattr(cfg, "auto_retry_max_delay_seconds", 3600) or 3600),
        )
        return base_delay, max_delay

    async def schedule_automatic_retry_after_failure(
        self,
        task_uuid: str,
        exc: BaseException,
    ) -> bool:
        """Park one eligible scheduled crawl until its bounded retry is due."""

        classification = classify_ingestion_failure(exc)
        base_delay, max_delay = self._automatic_retry_delays()
        now = datetime.now(ZoneInfo("UTC"))
        scheduled = False
        retry_payload: dict[str, Any] = {}
        job_id = ""

        async with get_database() as db:
            task = (
                await db.execute(
                    select(Task)
                    .where(Task.task_uuid == task_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if task is None or task.task_type != TaskType.CRAWL_DATA:
                return False
            if task.status != TaskStatus.FAILED:
                return task.status == TaskStatus.RETRYING

            input_data = dict(task.input_data or {})
            metadata = dict(task.metadata_ or {})
            retry = dict(metadata.get("ingestion_automatic_retry") or {})
            job_id = str(input_data.get("automation_job_id") or "").strip()
            retry_threshold = max(
                1,
                int(
                    input_data.get("retry_threshold")
                    or self._config().default_retry_threshold
                ),
            )
            max_scheduled_attempts = max(0, retry_threshold - 1)
            prior_scheduled = max(0, int(retry.get("scheduled_attempts") or 0))
            failure_count = max(1, int(task.retry_count or 0))
            eligible_trigger = automatic_ingestion_trigger_eligible(input_data)
            scheduled = bool(
                eligible_trigger
                and classification.retryable
                and failure_count < retry_threshold
                and prior_scheduled < max_scheduled_attempts
            )

            retry_payload = {
                "policy_version": "ingestion-transient-v1",
                "eligible_trigger": eligible_trigger,
                "retryable": classification.retryable,
                "category": classification.category,
                "reason": classification.reason,
                "retry_threshold": retry_threshold,
                "max_scheduled_attempts": max_scheduled_attempts,
                "scheduled_attempts": prior_scheduled,
                "failure_count": failure_count,
                "last_failure_at": now.isoformat(),
                "last_error": str(exc)[-4000:],
            }
            if scheduled:
                retry_number = prior_scheduled + 1
                delay_seconds = min(
                    max_delay,
                    base_delay * (2 ** (retry_number - 1)),
                )
                next_attempt_at = now + timedelta(seconds=delay_seconds)
                retry_payload.update(
                    {
                        "status": "scheduled",
                        "scheduled_attempts": retry_number,
                        "delay_seconds": delay_seconds,
                        "next_attempt_at": next_attempt_at.isoformat(),
                        "exhausted": False,
                    }
                )
                task.status = TaskStatus.RETRYING
            else:
                retry_payload.update(
                    {
                        "status": "terminal",
                        "next_attempt_at": None,
                        "exhausted": bool(
                            eligible_trigger
                            and classification.retryable
                            and (
                                failure_count >= retry_threshold
                                or prior_scheduled >= max_scheduled_attempts
                            )
                        ),
                    }
                )
            metadata["ingestion_automatic_retry"] = retry_payload
            task.metadata_ = metadata
            await db.commit()

        if scheduled:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="warning",
                title="Automatic Ingestion Retry Scheduled",
                content=(
                    f"{classification.reason}; retry "
                    f"{retry_payload['scheduled_attempts']}/{retry_payload['max_scheduled_attempts']} "
                    f"will be eligible at {retry_payload['next_attempt_at']}."
                ),
                content_type="text",
                metadata={
                    "event": "ingestion_auto_retry_scheduled",
                    "automation_job_id": job_id,
                    **retry_payload,
                },
            )
            logger.warning(
                "Automatic ingestion retry scheduled task={} job={} attempt={}/{} at={}",
                task_uuid,
                job_id,
                retry_payload["scheduled_attempts"],
                retry_payload["max_scheduled_attempts"],
                retry_payload["next_attempt_at"],
            )
        return scheduled

    async def requeue_due_automatic_retries(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> list[str]:
        """Atomically expose due ingestion retries to task workers."""

        current = _as_aware_utc(now) or datetime.now(ZoneInfo("UTC"))
        requeued: list[tuple[str, str, dict[str, Any]]] = []
        async with get_database() as db:
            retrying = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.task_type == TaskType.CRAWL_DATA,
                        Task.status == TaskStatus.RETRYING,
                    )
                    .order_by(Task.created_at.asc())
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            for task in retrying:
                input_data = dict(task.input_data or {})
                metadata = dict(task.metadata_ or {})
                retry = dict(metadata.get("ingestion_automatic_retry") or {})
                next_attempt_raw = str(retry.get("next_attempt_at") or "").strip()
                try:
                    next_attempt = _as_aware_utc(datetime.fromisoformat(next_attempt_raw))
                except (TypeError, ValueError):
                    next_attempt = None

                if (
                    retry.get("status") != "scheduled"
                    or next_attempt is None
                    or not automatic_ingestion_trigger_eligible(input_data)
                ):
                    retry.update(
                        {
                            "status": "terminal",
                            "next_attempt_at": None,
                            "reason": "invalid or ineligible persisted automatic retry reservation",
                        }
                    )
                    metadata["ingestion_automatic_retry"] = retry
                    task.metadata_ = metadata
                    task.status = TaskStatus.FAILED
                    continue
                if next_attempt > current:
                    continue

                active = (
                    await db.execute(
                        select(Task.id)
                        .where(
                            Task.id != task.id,
                            Task.task_type == TaskType.CRAWL_DATA,
                            Task.country_id == task.country_id,
                            Task.status.in_(
                                [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING]
                            ),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if active is not None:
                    continue

                retry.update(
                    {
                        "status": "queued",
                        "queued_at": current.isoformat(),
                        "next_attempt_at": None,
                    }
                )
                metadata["ingestion_automatic_retry"] = retry
                task.metadata_ = metadata
                task.status = TaskStatus.QUEUED
                task.progress = 0
                task.completed_steps = 0
                task.started_at = None
                task.completed_at = None
                task.actual_duration = None
                requeued.append(
                    (
                        task.task_uuid,
                        str(input_data.get("automation_job_id") or "").strip(),
                        retry,
                    )
                )
            await db.commit()

        for task_uuid, job_id, retry in requeued:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="info",
                title="Automatic Ingestion Retry Queued",
                content=(
                    f"Retry {retry.get('scheduled_attempts')}/"
                    f"{retry.get('max_scheduled_attempts')} is now queued for the task worker."
                ),
                content_type="text",
                metadata={
                    "event": "ingestion_auto_retry_queued",
                    "automation_job_id": job_id,
                    **retry,
                },
            )
            await task_manager._broadcast(
                {
                    "event": "task_status",
                    "task_uuid": task_uuid,
                    "status": TaskStatus.QUEUED.value,
                    "progress": 0,
                    "automatic_retry": True,
                }
            )
        return [task_uuid for task_uuid, _job_id, _retry in requeued]

    async def mark_automatic_retry_succeeded(self, task_uuid: str) -> bool:
        """Close retry metadata after the same crawl task succeeds."""

        changed = False
        async with get_database() as db:
            task = (
                await db.execute(select(Task).where(Task.task_uuid == task_uuid))
            ).scalar_one_or_none()
            if task is None or task.task_type != TaskType.CRAWL_DATA:
                return False
            metadata = dict(task.metadata_ or {})
            retry = dict(metadata.get("ingestion_automatic_retry") or {})
            if retry.get("scheduled_attempts") and retry.get("status") != "succeeded":
                retry.update(
                    {
                        "status": "succeeded",
                        "succeeded_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                        "next_attempt_at": None,
                    }
                )
                metadata["ingestion_automatic_retry"] = retry
                task.metadata_ = metadata
                await db.commit()
                changed = True
        if changed:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="success",
                title="Automatic Ingestion Retry Recovered",
                content="The scheduled retry completed without weakening any source or quality gate.",
                content_type="text",
                metadata={"event": "ingestion_auto_retry_succeeded"},
            )
        return changed

    async def trigger_job(self, job_id: str, *, manual: bool) -> dict[str, Any]:
        async with self._lock:
            job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
            if job is None:
                raise ValueError(f"Automation job not found: {job_id}")

            tz_name = job.timezone or self._config().timezone
            now = datetime.now(ZoneInfo(tz_name))
            state = self._states.setdefault(job.job_id, AutomationJobState())
            state.last_started_at = now
            state.last_error = None
            state.last_status = "running"

            try:
                result = await crawl_task_service.enqueue_crawl_task(
                    country_code=job.country_code,
                    source=job.source,
                    force=job.force,
                    process=job.process,
                    save_raw=job.save_raw,
                    fill_missing=job.fill_missing,
                    include_current_month=job.include_current_month,
                    revision_window_months=job.revision_window_months,
                    priority=job.priority,
                    metadata={
                        "automation_job_id": job.job_id,
                        "automation_job_name": job.name,
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                        "retry_threshold": job.retry_threshold,
                    },
                )
                state.last_finished_at = datetime.now(ZoneInfo(tz_name))
                state.last_task_uuid = result.task.task_uuid
                if result.created:
                    state.run_count += 1
                    state.last_status = "queued"
                    state.next_run_at = self._compute_next_run(job, now=state.last_finished_at)
                    return {
                        "job_id": job.job_id,
                        "status": "queued",
                        "task_uuid": result.task.task_uuid,
                    }

                state.skipped_count += 1
                state.last_status = "skipped"
                state.last_error = result.skipped_reason or "Task already running"
                state.next_run_at = self._compute_next_run(job, now=state.last_finished_at)
                return {
                    "job_id": job.job_id,
                    "status": "skipped",
                    "task_uuid": result.task.task_uuid,
                    "reason": result.skipped_reason,
                }
            except Exception as exc:
                state.last_finished_at = datetime.now(ZoneInfo(tz_name))
                state.last_status = "failed"
                state.last_error = str(exc)
                state.next_run_at = self._compute_next_run(job, now=state.last_finished_at)
                logger.error("Automation job {} failed: {}", job.job_id, exc)
                raise
            finally:
                await schedule_state_repository.save("ingestion", job.job_id, state)

    def snapshot(self) -> dict[str, Any]:
        raise RuntimeError("Use snapshot_async() for automation config snapshots")

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        smtp_state = system_settings_service.build_smtp_status()
        await self.ensure_storage()
        jobs = await self.load_jobs()
        job_ids = {job.job_id for job in jobs}
        latest_task_by_job_id: dict[str, Task] = {}
        latest_success_by_job_id: dict[str, Task] = {}
        if job_ids:
            async with get_database() as db:
                recent_tasks = (
                    await db.execute(
                        select(Task)
                        .where(Task.task_type == TaskType.CRAWL_DATA)
                        .order_by(Task.created_at.desc())
                        .limit(max(200, len(job_ids) * 20))
                    )
                ).scalars().all()
            for task in recent_tasks:
                inp = task.input_data if isinstance(task.input_data, dict) else {}
                job_id = str(inp.get("automation_job_id") or "").strip()
                if job_id in job_ids and job_id not in latest_task_by_job_id:
                    latest_task_by_job_id[job_id] = task
                if (
                    job_id in job_ids
                    and job_id not in latest_success_by_job_id
                    and _enum_value(task.status) == TaskStatus.COMPLETED.value
                ):
                    latest_success_by_job_id[job_id] = task

        jobs_payload: list[dict[str, Any]] = []
        for job in jobs:
            state = self._states.setdefault(job.job_id, AutomationJobState())
            self._sync_state_schedule(job, state, reset=False)
            latest_task = latest_task_by_job_id.get(job.job_id)
            latest_success = latest_success_by_job_id.get(job.job_id)
            if latest_task is not None:
                state.last_task_uuid = latest_task.task_uuid
                state.last_status = _enum_value(latest_task.status)
                state.last_started_at = latest_task.started_at or state.last_started_at
                state.last_finished_at = latest_task.completed_at or state.last_finished_at
                state.last_error = latest_task.last_error
            jobs_payload.append(
                {
                    **asdict(job),
                    **_ingestion_job_health(job, latest_task, latest_success),
                    "next_run_at": _iso(state.next_run_at),
                    "last_started_at": _iso(state.last_started_at),
                    "last_finished_at": _iso(state.last_finished_at),
                    "last_status": state.last_status,
                    "last_error": state.last_error,
                    "last_task_uuid": state.last_task_uuid,
                    "run_count": state.run_count,
                    "skipped_count": state.skipped_count,
                    "automatic_retry": (
                        dict((latest_task.metadata_ or {}).get("ingestion_automatic_retry") or {})
                        if latest_task is not None
                        else {}
                    ),
                }
            )
        return {
            "enabled": cfg.enabled,
            "timezone": cfg.timezone,
            "poll_interval_seconds": cfg.poll_interval_seconds,
            "default_retry_threshold": cfg.default_retry_threshold,
            "admin_emails": smtp_state["admin_emails"],
            "email_enabled": smtp_state["alerting_ready"],
            "last_tick_at": _iso(self._last_tick_at),
            "jobs": jobs_payload,
        }

    async def notify_task_retry_if_needed(self, task_uuid: str) -> None:
        """Send a warning email when a task is being retried.
        
        This is called when a task fails but hasn't reached the retry threshold yet.
        It warns administrators that the task is experiencing issues but the system
        is handling it automatically.
        """
        cfg = self._config()
        smtp_state = system_settings_service.build_smtp_status()
        if not smtp_state["admin_emails"] or not smtp_state["alerting_ready"]:
            return

        async with get_database() as db:
            task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one_or_none()
            if task is None:
                return

            metadata = dict(task.metadata_ or {})
            retry_count = int(task.retry_count or 0)
            
            # Only send warning if retry_count > 0
            if retry_count <= 0:
                return

            retry_threshold = int(
                (task.input_data or {}).get("retry_threshold")
                or cfg.default_retry_threshold
            )
            
            # Don't send if we've already reached the threshold
            if retry_count >= retry_threshold:
                return

            # Debounce: don't send more than one warning per minute
            last_retry_warning = metadata.get("last_retry_warning_at")
            if last_retry_warning:
                from datetime import datetime as dt
                try:
                    last_warning_time = dt.fromisoformat(last_retry_warning)
                    if (dt.utcnow() - last_warning_time).total_seconds() < 60:
                        return
                except (ValueError, TypeError):
                    pass

            country = await db.get(Country, task.country_id) if task.country_id else None
            
            from src.generation.email_service import EmailService
            body = EmailService.build_task_retry_warning_html(
                task_name=task.task_name,
                task_uuid=task.task_uuid,
                task_type=str(task.task_type.value if hasattr(task.task_type, 'value') else task.task_type),
                country=country.code if country else "-",
                retry_count=retry_count,
                retry_threshold=retry_threshold,
                last_error=task.last_error or "Unknown error",
            )
            
            sent = smtp_email_service.send_email(
                recipients=smtp_state["admin_emails"],
                subject=f"[GlobalID] Task retry warning ({retry_count}/{retry_threshold}): {task.task_name}",
                body_html=body,
            )
            
            if sent:
                from datetime import datetime as dt
                metadata["last_retry_warning_at"] = dt.utcnow().isoformat()
                task.metadata_ = metadata
                await db.commit()
                logger.info(f"Retry warning sent for task {task_uuid} (attempt {retry_count}/{retry_threshold})")

    async def notify_task_failure_if_needed(self, task_uuid: str) -> None:
        cfg = self._config()
        smtp_state = system_settings_service.build_smtp_status()
        if not smtp_state["admin_emails"] or not smtp_state["alerting_ready"]:
            return

        async with get_database() as db:
            task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one_or_none()
            if task is None:
                return

            metadata = dict(task.metadata_ or {})
            if metadata.get("failure_notification_sent_at"):
                return

            retry_threshold = int(
                (task.input_data or {}).get("retry_threshold")
                or cfg.default_retry_threshold
            )
            if int(task.retry_count or 0) < retry_threshold:
                return

            workbook_entries = (
                await db.execute(
                    select(TaskWorkbook)
                    .where(TaskWorkbook.task_id == task.id)
                    .order_by(TaskWorkbook.created_at.asc())
                )
            ).scalars().all()
            country = await db.get(Country, task.country_id) if task.country_id else None

            body = self._build_failure_email_html(
                task=task,
                country=country.code if country else None,
                workbook_entries=workbook_entries,
                retry_threshold=retry_threshold,
                task_uuid=task_uuid,
            )
            attachments = self._collect_task_log_attachments(task_uuid)
            sent = smtp_email_service.send_email(
                recipients=smtp_state["admin_emails"],
                subject=f"[GlobalID] Task failed after retries: {task.task_name}",
                body_html=body,
                attachments=attachments,
            )
            if not sent:
                return

            metadata["failure_notification_sent_at"] = (
                datetime.now(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            )
            task.metadata_ = metadata
            await db.commit()

    def _build_failure_email_html(
        self,
        *,
        task: Task,
        country: Optional[str],
        workbook_entries: list[TaskWorkbook],
        retry_threshold: int,
        task_uuid: Optional[str] = None,
    ) -> str:
        latest_entries = workbook_entries[-12:]
        entry_blocks = []
        for entry in latest_entries:
            title = html.escape(entry.title or entry.entry_type or "log")
            content = html.escape((entry.content or "").strip())[:4000]
            entry_blocks.append(
                f"<li><strong>{title}</strong><br/><pre style='white-space:pre-wrap'>{content}</pre></li>"
            )
        log_tail = html.escape(self._read_recent_error_log(task_uuid or task.task_uuid))
        input_data = html.escape(json.dumps(task.input_data or {}, ensure_ascii=False, indent=2))
        last_error = html.escape(task.last_error or "Unknown error")
        country_text = html.escape(country or "-")
        return f"""
<html>
  <body style="font-family:Segoe UI,Arial,sans-serif;line-height:1.5;color:#1f2937">
    <h2 style="margin-bottom:8px">GlobalID task failure alert</h2>
    <p>The task exceeded the configured retry threshold and needs attention.</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse">
      <tr><td><strong>Task</strong></td><td>{html.escape(task.task_name)}</td></tr>
      <tr><td><strong>Task UUID</strong></td><td><code>{html.escape(task.task_uuid)}</code></td></tr>
      <tr><td><strong>Type</strong></td><td>{html.escape(str(task.task_type))}</td></tr>
      <tr><td><strong>Status</strong></td><td>{html.escape(str(task.status))}</td></tr>
      <tr><td><strong>Country</strong></td><td>{country_text}</td></tr>
      <tr><td><strong>Retry Count</strong></td><td>{int(task.retry_count or 0)} / threshold {retry_threshold}</td></tr>
      <tr><td><strong>Priority</strong></td><td>{html.escape(str(task.priority))}</td></tr>
      <tr><td><strong>Created</strong></td><td>{html.escape(str(task.created_at or '-'))}</td></tr>
      <tr><td><strong>Started</strong></td><td>{html.escape(str(task.started_at or '-'))}</td></tr>
      <tr><td><strong>Completed</strong></td><td>{html.escape(str(task.completed_at or '-'))}</td></tr>
      <tr><td><strong>Last Error</strong></td><td>{last_error}</td></tr>
    </table>
    <h3>Input</h3>
    <pre style='white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:8px'>{input_data}</pre>
    <h3>Recent workbook entries</h3>
    <ol>{''.join(entry_blocks) or '<li>No workbook entries found.</li>'}</ol>
    <h3>Recent error log tail</h3>
    <pre style='white-space:pre-wrap;background:#111827;color:#f9fafb;padding:12px;border-radius:8px'>{log_tail}</pre>
  </body>
</html>
"""

    def _compute_next_run(
        self,
        job: AutomationJobConfig,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        timezone_name = job.timezone or self._config().timezone
        zone = ZoneInfo(timezone_name)
        current = now.astimezone(zone) if now else datetime.now(zone)
        if job.interval_minutes:
            return current + timedelta(minutes=max(1, job.interval_minutes))
        if job.daily_time:
            hour_text, minute_text = job.daily_time.split(":", 1)
            scheduled = datetime.combine(
                current.date(),
                time(hour=int(hour_text), minute=int(minute_text), tzinfo=zone),
            )
            if scheduled <= current:
                scheduled += timedelta(days=1)
            return scheduled
        return current + timedelta(days=1)

    def _read_recent_error_log(self, task_uuid: Optional[str] = None) -> str:
        """Read recent error log lines, optionally filtered by task_uuid.
        
        When task_uuid is provided, searches all error log files for lines
        containing that UUID to provide task-specific context.
        Falls back to reading the latest error log file if no matches found.
        """
        log_dir = Path(get_config().log_dir)
        candidates = sorted(log_dir.glob("error_*.log"))
        if not candidates:
            return "No error log file found."
        
        if task_uuid:
            # Search across all error logs for task-specific entries
            task_lines = []
            for log_file in candidates:
                try:
                    with log_file.open("r", encoding="utf-8", errors="ignore") as handle:
                        for line in handle:
                            if task_uuid in line:
                                task_lines.append(line.rstrip())
                except Exception:
                    continue
            
            if task_lines:
                return "\n".join(task_lines[-120:]).strip()
        
        # Fallback: read last 120 lines of the latest error log
        latest = candidates[-1]
        try:
            with latest.open("r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
            return "".join(lines[-120:]).strip() or f"{latest.name} is empty."
        except Exception as exc:
            return f"Failed to read error log {latest.name}: {exc}"

    def _collect_task_log_attachments(self, task_uuid: str) -> list[str]:
        """Collect log files as attachments for failure notification emails.
        
        Returns list of file paths to attach:
        - Latest error log file
        - Task-specific log if available
        """
        attachments = []
        log_dir = Path(get_config().log_dir)
        
        # Attach latest error log
        error_logs = sorted(log_dir.glob("error_*.log"))
        if error_logs:
            attachments.append(str(error_logs[-1]))
        
        # Attach main log from today
        main_logs = sorted(log_dir.glob("globalid_*.log"))
        if main_logs:
            attachments.append(str(main_logs[-1]))
        
        return attachments


automation_service = AutomationService()
