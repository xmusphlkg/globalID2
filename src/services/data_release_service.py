"""Data release workflow orchestration for site export, Git push, and Pages deploy."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import signal  # Re-exported for compatibility with existing diagnostics/tests.
import sys
from typing import Any, Optional
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.core import get_config, get_database, get_logger
from src.core.data_share import (
    get_data_share_raw_base_url,
    get_data_share_repo_branch,
    get_data_share_repo_url,
)
from src.core.task_manager import task_manager
from src.domain import DataReleaseJob, Task, TaskPriority, TaskStatus, TaskType
from src.services.data_release import checks as release_checks
from src.services.data_release import pipeline as release_pipeline
from src.services.data_release.commands import (
    build_cloudflare_deploy_command,
    build_generate_site_data_command,
    build_publish_download_repo_command,
    build_publish_raw_archive_command,
    build_update_situation_room_command,
    build_validate_situation_release_command,
)
from src.services.data_release.process_runner import (
    run_capture,
    run_logged_command,
    terminate_process_tree,
)
from src.services.data_release.resilience import (
    automatic_trigger_eligible,
    classify_release_failure,
)
from src.services.exceptions import TaskCancelledError
from src.services.settings_service import system_settings_service
from src.control_plane.schedule_state import schedule_state_repository

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv is part of project requirements.
    dotenv_values = None

logger = get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
ASTRO_DIR = ROOT_DIR / "astro-site"
ENV_PATH = ROOT_DIR / ".env"
SUBSCRIPTIONS_SCRIPT = ROOT_DIR / "cloudflare" / "subscriptions" / "scripts" / "wrangler-env.sh"
GENERATED_DATA_PATHS = (
    "astro-site/src/data/about.json",
    "astro-site/src/data/countries",
    "astro-site/src/data/disease-knowledge",
    "astro-site/src/data/disease-ontology.json",
    "astro-site/src/data/diseases",
    "astro-site/src/data/downloads.json",
    "astro-site/src/data/meta.json",
    "astro-site/src/data/reports",
    "astro-site/src/data/research",
    "astro-site/src/data/situation",
    "data/current",
    "data/raw",
)
SITE_RELEASE_MANIFEST = ASTRO_DIR / "dist" / "release.json"
SITE_VISUAL_MODULE_PREFIXES = (
    "ChartFrame.",
    "ComparisonTable.",
    "DiseaseCountryCurve.",
    "DiseaseHeatmap.",
    "DiseaseMonthlyBar.",
    "EpidemicCurve.",
)
AUTO_RELEASE_TASK_TYPES = (
    TaskType.CRAWL_DATA,
    TaskType.PROCESS_DATA,
    TaskType.GENERATE_REPORT,
    TaskType.SYNC_LITERATURE,
    TaskType.ENRICH_LITERATURE,
    TaskType.DISCOVER_LITERATURE_GAPS,
)
LITERATURE_RELEASE_TASK_TYPES = {
    TaskType.SYNC_LITERATURE,
    TaskType.ENRICH_LITERATURE,
    TaskType.DISCOVER_LITERATURE_GAPS,
}
GITHUB_SSH_PREFIXES = ("git@github.com:", "ssh://git@github.com/")
SUBSCRIPTION_SYNC_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
SUBSCRIPTION_SYNC_STRICT_VALUES = {"1", "true", "yes", "on", "required", "strict", "force"}
LOGGED_COMMAND_CANCEL_POLL_SECONDS = 1.0
DEFAULT_LOGGED_COMMAND_TIMEOUT_SECONDS = 15 * 60
GENERATE_SITE_DATA_TIMEOUT_SECONDS = 30 * 60
ASTRO_BUILD_TIMEOUT_SECONDS = 15 * 60
CLOUDFLARE_DEPLOY_TIMEOUT_SECONDS = 15 * 60
SUBSCRIPTION_SYNC_TIMEOUT_SECONDS = 10 * 60
DOWNLOAD_PUBLISH_TIMEOUT_SECONDS = 15 * 60
DOWNLOAD_REPO_BRANCH = get_data_share_repo_branch()
DIRECT_DOWNLOAD_DIR = ROOT_DIR / "exports" / "site-downloads"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _coerce_status(value: Any, fallback: str = "idle") -> str:
    if hasattr(value, "value"):
        return str(value.value)
    if value is None:
        return fallback
    return str(value)


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


@dataclass
class DataReleaseJobConfig:
    job_id: str
    name: str
    enabled: bool = True
    priority: str = "high"
    auto_after_crawls: bool = False
    # When enabled, publish the validated v2 snapshot after local generation.
    include_git_push: bool = True
    include_cloudflare_deploy: bool = True
    require_clean_worktree: bool = True
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = "02:00"
    timezone: Optional[str] = "UTC"
    github_remote: str = "origin"
    github_branch: Optional[str] = None
    cloudflare_project_name: Optional[str] = None
    commit_message_template: str = "chore(data-release): publish site data {timestamp}"
    notes: Optional[str] = None


@dataclass
class DataReleaseJobState:
    next_run_at: Optional[datetime] = None
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_status: str = "idle"
    last_error: Optional[str] = None
    last_task_uuid: Optional[str] = None
    run_count: int = 0
    skipped_count: int = 0


class DataReleaseService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._storage_ready = False
        self._storage_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._states: dict[str, DataReleaseJobState] = {}
        self._last_tick_at: Optional[datetime] = None

    def _config(self):
        return get_config().data_release

    async def ensure_storage(self) -> None:
        """Verify managed storage and seed configuration without running DDL."""
        if self._storage_ready:
            return
        async with self._storage_lock:
            if self._storage_ready:
                return
            try:
                async with get_database() as db:
                    await db.execute(select(DataReleaseJob.id).limit(1))
                await self._seed_default_job_if_needed()
            except Exception as exc:
                raise RuntimeError(
                    "data_release_jobs storage is not ready; run the Alembic preflight and upgrade"
                ) from exc
            self._storage_ready = True

    async def _seed_default_job_if_needed(self) -> None:
        cfg = self._config()
        github_settings = system_settings_service.github_runtime()
        cloudflare_settings = system_settings_service.cloudflare_runtime()
        async with get_database() as db:
            existing = (
                await db.execute(select(DataReleaseJob.id).limit(1))
            ).scalar_one_or_none()
            if existing is not None:
                return

            db.add(
                DataReleaseJob(
                    job_id="site-release",
                    name="Site Data Release",
                    enabled=True,
                    priority="high",
                    auto_after_crawls=False,
                    include_git_push=True,
                    include_cloudflare_deploy=True,
                    require_clean_worktree=True,
                    daily_time="02:00",
                    interval_minutes=None,
                    timezone="UTC",
                    github_remote=github_settings["default_github_remote"] or "origin",
                    github_branch=get_data_share_repo_branch(),
                    cloudflare_project_name=cloudflare_settings["default_cloudflare_project_name"] or "globalid",
                    commit_message_template=cfg.default_commit_message_template,
                    notes="Default 02:00 UTC release pipeline for one daily Situation Room refresh, static export, build, and Cloudflare Pages deploy.",
                )
            )
            await db.commit()

    async def load_jobs(self) -> list[DataReleaseJobConfig]:
        await self.ensure_storage()
        cfg = self._config()
        github_settings = system_settings_service.github_runtime()
        cloudflare_settings = system_settings_service.cloudflare_runtime()
        async with get_database() as db:
            rows = (
                await db.execute(select(DataReleaseJob).order_by(DataReleaseJob.job_id.asc()))
            ).scalars().all()

        jobs: list[DataReleaseJobConfig] = []
        for row in rows:
            resolved_branch = self._normalize_download_repo_branch(row.github_branch)
            resolved_project_name = self._normalize_cloudflare_project_name(row.cloudflare_project_name)
            jobs.append(
                DataReleaseJobConfig(
                    job_id=row.job_id,
                    name=row.name,
                    enabled=row.enabled,
                    priority=row.priority or "high",
                    auto_after_crawls=row.auto_after_crawls,
                    include_git_push=row.include_git_push,
                    include_cloudflare_deploy=row.include_cloudflare_deploy,
                    require_clean_worktree=row.require_clean_worktree,
                    interval_minutes=row.interval_minutes,
                    daily_time=row.daily_time,
                    timezone=row.timezone or cfg.timezone,
                    github_remote=row.github_remote or github_settings["default_github_remote"] or "origin",
                    github_branch=resolved_branch,
                    cloudflare_project_name=resolved_project_name or cloudflare_settings["default_cloudflare_project_name"] or "globalid",
                    commit_message_template=row.commit_message_template or cfg.default_commit_message_template,
                    notes=row.notes,
                )
            )
        return jobs

    def _sync_state_schedule(
        self,
        job: DataReleaseJobConfig,
        state: DataReleaseJobState,
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
            state = self._states.setdefault(job.job_id, DataReleaseJobState())
            self._sync_state_schedule(job, state, reset=True)
            await schedule_state_repository.save("release", job.job_id, state)

    async def remove_job_state(self, job_id: str) -> None:
        async with self._lock:
            self._states.pop(job_id, None)
            await schedule_state_repository.remove("release", job_id)

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.enabled:
            logger.info("Data release scheduler disabled by configuration")
            return
        await self.ensure_storage()
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        persisted = await schedule_state_repository.load("release")
        for job in await self.load_jobs():
            state = self._states.setdefault(job.job_id, DataReleaseJobState())
            for field, value in persisted.get(job.job_id, {}).items():
                setattr(state, field, value)
            self._sync_state_schedule(job, state, reset=state.next_run_at is None)
            await schedule_state_repository.save("release", job.job_id, state)
        self._task = asyncio.create_task(self._run_loop(), name="globalid-data-release-scheduler")
        logger.info("Data release scheduler started with %s configured job(s)", len(await self.load_jobs()))

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
                    state = self._states.setdefault(job.job_id, DataReleaseJobState())
                    now = datetime.now(ZoneInfo(job.timezone or cfg.timezone))
                    self._sync_state_schedule(job, state, reset=state.next_run_at is None)
                    if state.next_run_at and now >= state.next_run_at:
                        await self.trigger_job(job.job_id, manual=False, trigger="scheduled")
            except Exception as exc:
                logger.exception("Data release scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=cfg.poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    def _automatic_retry_settings(self) -> tuple[int, int, int]:
        cfg = self._config()
        attempts = max(0, int(getattr(cfg, "auto_retry_max_attempts", 3) or 0))
        base_delay = max(
            5,
            int(getattr(cfg, "auto_retry_base_delay_seconds", 300) or 300),
        )
        max_delay = max(
            base_delay,
            int(getattr(cfg, "auto_retry_max_delay_seconds", 3600) or 3600),
        )
        return attempts, base_delay, max_delay

    async def schedule_automatic_retry_after_failure(
        self,
        task_uuid: str,
        exc: BaseException,
    ) -> bool:
        """Persist a delayed retry for one eligible automatic release failure.

        The task's existing ``retrying`` state is the durable reservation.  It
        blocks normal scheduled/upstream enqueue paths for the same release job
        while remaining invisible to task workers until the scheduler changes
        it back to ``queued`` at ``next_attempt_at``.
        """

        classification = classify_release_failure(exc)
        max_attempts, base_delay, max_delay = self._automatic_retry_settings()
        now = datetime.now(ZoneInfo("UTC"))
        scheduled = False
        entry_payload: dict[str, Any] = {}
        job_id = ""

        async with get_database() as db:
            task = (
                await db.execute(
                    select(Task)
                    .where(Task.task_uuid == task_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if task is None or task.task_type != TaskType.EXPORT_DATA:
                return False
            if task.status != TaskStatus.FAILED:
                return task.status == TaskStatus.RETRYING

            input_data = dict(task.input_data or {})
            metadata = dict(task.metadata_ or {})
            job_id = str(
                metadata.get("release_job_id")
                or input_data.get("release_job_id")
                or ""
            ).strip()
            retry = dict(metadata.get("automatic_retry") or {})
            prior_scheduled = max(0, int(retry.get("scheduled_attempts") or 0))
            eligible_trigger = automatic_trigger_eligible(input_data)
            scheduled = bool(
                eligible_trigger
                and classification.retryable
                and prior_scheduled < max_attempts
            )

            entry_payload = {
                "policy_version": "release-transient-v1",
                "eligible_trigger": eligible_trigger,
                "retryable": classification.retryable,
                "category": classification.category,
                "stage": classification.stage,
                "reason": classification.reason,
                "max_attempts": max_attempts,
                "scheduled_attempts": prior_scheduled,
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
                entry_payload.update(
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
                entry_payload.update(
                    {
                        "status": "terminal",
                        "next_attempt_at": None,
                        "exhausted": bool(
                            eligible_trigger
                            and classification.retryable
                            and prior_scheduled >= max_attempts
                        ),
                    }
                )
            metadata["automatic_retry"] = entry_payload
            task.metadata_ = metadata
            await db.commit()

        if scheduled:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="warning",
                title="Automatic Release Retry Scheduled",
                content=(
                    f"Transient {classification.stage or 'external'} failure; "
                    f"retry {entry_payload['scheduled_attempts']}/{max_attempts} will be "
                    f"eligible at {entry_payload['next_attempt_at']}."
                ),
                content_type="text",
                metadata={
                    "event": "release_auto_retry_scheduled",
                    "release_job_id": job_id,
                    **entry_payload,
                },
            )
            logger.warning(
                "Automatic release retry scheduled task=%s job=%s attempt=%s/%s at=%s",
                task_uuid,
                job_id,
                entry_payload["scheduled_attempts"],
                max_attempts,
                entry_payload["next_attempt_at"],
            )
        return scheduled

    async def requeue_due_automatic_retries(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> list[str]:
        """Atomically make persisted, due release retries visible to workers."""

        current = _as_aware_utc(now) or datetime.now(ZoneInfo("UTC"))
        requeued: list[tuple[str, str, dict[str, Any]]] = []
        async with get_database() as db:
            retrying = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.task_type == TaskType.EXPORT_DATA,
                        Task.status == TaskStatus.RETRYING,
                    )
                    .order_by(Task.created_at.asc())
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            for task in retrying:
                input_data = dict(task.input_data or {})
                metadata = dict(task.metadata_ or {})
                retry = dict(metadata.get("automatic_retry") or {})
                next_attempt_raw = str(retry.get("next_attempt_at") or "").strip()
                try:
                    next_attempt = _as_aware_utc(datetime.fromisoformat(next_attempt_raw))
                except (TypeError, ValueError):
                    next_attempt = None

                # Never leave a malformed or manually-triggered task parked in
                # RETRYING forever.  It becomes terminal and follows the normal
                # alert/operator path instead of being guessed back into queue.
                if (
                    retry.get("status") != "scheduled"
                    or next_attempt is None
                    or not automatic_trigger_eligible(input_data)
                ):
                    retry.update(
                        {
                            "status": "terminal",
                            "next_attempt_at": None,
                            "reason": "invalid or ineligible persisted automatic retry reservation",
                        }
                    )
                    metadata["automatic_retry"] = retry
                    task.metadata_ = metadata
                    task.status = TaskStatus.FAILED
                    continue
                if next_attempt > current:
                    continue

                job_id = str(
                    metadata.get("release_job_id")
                    or input_data.get("release_job_id")
                    or ""
                ).strip()
                active = (
                    await db.execute(
                        select(Task.id)
                        .where(
                            Task.id != task.id,
                            Task.task_type == TaskType.EXPORT_DATA,
                            Task.status.in_(
                                [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING]
                            ),
                        )
                    )
                ).scalars().all()
                if active:
                    # A release worker is globally serialized because the
                    # generated artifact directories are shared across jobs.
                    continue

                retry.update(
                    {
                        "status": "queued",
                        "queued_at": current.isoformat(),
                        "next_attempt_at": None,
                    }
                )
                metadata["automatic_retry"] = retry
                task.metadata_ = metadata
                task.status = TaskStatus.QUEUED
                task.progress = 0
                task.completed_steps = 0
                task.started_at = None
                task.completed_at = None
                task.actual_duration = None
                # Preserve last_error for diagnosis until task_lifecycle marks
                # the retry RUNNING and clears it.
                requeued.append((task.task_uuid, job_id, retry))
            await db.commit()

        for task_uuid, job_id, retry in requeued:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="info",
                title="Automatic Release Retry Queued",
                content=(
                    f"Retry {retry.get('scheduled_attempts')}/{retry.get('max_attempts')} "
                    "is now queued for the task worker."
                ),
                content_type="text",
                metadata={
                    "event": "release_auto_retry_queued",
                    "release_job_id": job_id,
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

    async def trigger_job(
        self,
        job_id: str,
        *,
        manual: bool,
        trigger: str,
        trigger_task_uuid: Optional[str] = None,
    ) -> dict[str, Any]:
        async with self._lock:
            job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
            if job is None:
                raise ValueError(f"Data release job not found: {job_id}")

            tz_name = job.timezone or self._config().timezone
            now = datetime.now(ZoneInfo(tz_name))
            state = self._states.setdefault(job.job_id, DataReleaseJobState())
            state.last_started_at = now
            state.last_error = None
            state.last_status = "running"

            try:
                result = await self._enqueue_release_task(
                    job,
                    manual=manual,
                    trigger=trigger,
                    trigger_task_uuid=trigger_task_uuid,
                )
                state.last_finished_at = datetime.now(ZoneInfo(tz_name))
                state.last_task_uuid = result["task_uuid"]
                if result["status"] == "queued":
                    state.run_count += 1
                    state.last_status = "queued"
                else:
                    state.skipped_count += 1
                    state.last_status = result["status"]
                    state.last_error = result.get("reason")
                state.next_run_at = self._compute_next_run(job, now=state.last_finished_at)
                return result
            except Exception as exc:
                state.last_finished_at = datetime.now(ZoneInfo(tz_name))
                state.last_status = "failed"
                state.last_error = str(exc)
                state.next_run_at = self._compute_next_run(job, now=state.last_finished_at)
                logger.error("Data release job %s failed to queue: %s", job.job_id, exc)
                raise
            finally:
                await schedule_state_repository.save("release", job.job_id, state)

    async def maybe_trigger_after_task_completion(
        self,
        trigger_task_uuid: str,
        trigger_task_type: str | TaskType,
    ) -> None:
        await self.ensure_storage()
        if not self._config().enabled:
            return
        try:
            normalized_task_type = TaskType(trigger_task_type)
        except ValueError:
            return
        if normalized_task_type not in AUTO_RELEASE_TASK_TYPES:
            return
        if normalized_task_type in LITERATURE_RELEASE_TASK_TYPES:
            async with get_database() as db:
                trigger_task = (
                    await db.execute(select(Task).where(Task.task_uuid == trigger_task_uuid))
                ).scalar_one_or_none()
            output = dict((trigger_task.output_data if trigger_task else None) or {})
            automation = dict(output.get("automation") or {})
            public_changes = int(automation.get("changed") or 0)
            if normalized_task_type == TaskType.SYNC_LITERATURE:
                public_changes += int(output.get("published") or 0)
            if public_changes <= 0:
                return
        enabled_jobs = [job for job in await self.load_jobs() if job.enabled and job.auto_after_crawls]
        if not enabled_jobs:
            return

        async with get_database() as db:
            active_updates = (
                await db.execute(
                    select(Task.id).where(
                        Task.task_type.in_(AUTO_RELEASE_TASK_TYPES),
                        Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING]),
                    )
                )
            ).scalars().first()
            if active_updates is not None:
                return

            latest_data_completion = (
                await db.execute(
                    select(Task.completed_at)
                    .where(
                        Task.task_type.in_(AUTO_RELEASE_TASK_TYPES),
                        Task.status == TaskStatus.COMPLETED,
                        Task.completed_at.is_not(None),
                    )
                    .order_by(Task.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

        if latest_data_completion is None:
            return

        for job in enabled_jobs:
            if await self._release_up_to_date(job.job_id, latest_data_completion):
                continue
            cooldown_reason = await self._release_failure_cooldown_reason(job.job_id)
            if cooldown_reason:
                state = self._states.setdefault(job.job_id, DataReleaseJobState())
                state.skipped_count += 1
                state.last_status = "cooldown"
                state.last_error = cooldown_reason
                state.last_finished_at = datetime.now(ZoneInfo(job.timezone or self._config().timezone))
                logger.warning("Auto-triggered data release %s suppressed: %s", job.job_id, cooldown_reason)
                continue
            try:
                await self.trigger_job(
                    job.job_id,
                    manual=False,
                    trigger="upstream_completion",
                    trigger_task_uuid=trigger_task_uuid,
                )
            except Exception as exc:
                logger.warning("Auto-triggered data release %s was skipped: %s", job.job_id, exc)

    async def maybe_trigger_after_crawl_completion(self, crawl_task_uuid: str) -> None:
        await self.maybe_trigger_after_task_completion(crawl_task_uuid, TaskType.CRAWL_DATA)

    async def _release_up_to_date(self, job_id: str, crawl_completed_at: datetime) -> bool:
        async with get_database() as db:
            tasks = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.task_type == TaskType.EXPORT_DATA,
                        Task.status == TaskStatus.COMPLETED,
                    )
                    .order_by(Task.created_at.desc())
                )
            ).scalars().all()
        for task in tasks:
            metadata = dict(task.metadata_ or {})
            if metadata.get("release_job_id") != job_id:
                continue
            task_time = _as_aware_utc(task.completed_at or task.created_at)
            crawl_time = _as_aware_utc(crawl_completed_at)
            return bool(task_time and crawl_time and task_time >= crawl_time)
        return False

    async def _release_failure_cooldown_reason(self, job_id: str) -> Optional[str]:
        cooldown_minutes = int(self._config().auto_failure_cooldown_minutes or 0)
        if cooldown_minutes <= 0:
            return None

        now = datetime.now(ZoneInfo("UTC"))
        cutoff = now - timedelta(minutes=cooldown_minutes)
        task = await self._latest_terminal_release_task(job_id)
        if task is None or task.status != TaskStatus.FAILED:
            return None

        task_time = _as_aware_utc(task.completed_at or task.created_at)
        if task_time is None or task_time < cutoff:
            return None
        until = task_time + timedelta(minutes=cooldown_minutes)
        return (
            f"latest release task {task.task_uuid} failed at {task_time.isoformat()}; "
            f"auto release is cooling down until {until.isoformat()}"
        )

    async def _latest_terminal_release_task(self, job_id: str) -> Optional[Task]:
        async with get_database() as db:
            result = await db.execute(
                select(Task)
                .where(
                    Task.task_type == TaskType.EXPORT_DATA,
                    Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
                )
                .order_by(Task.completed_at.desc().nullslast(), Task.created_at.desc())
                .limit(100)
            )
            tasks = list(result.scalars().all())
        for task in tasks:
            metadata = dict(task.metadata_ or {})
            if metadata.get("release_job_id") == job_id:
                return task
        return None

    async def _enqueue_release_task(
        self,
        job: DataReleaseJobConfig,
        *,
        manual: bool,
        trigger: str,
        trigger_task_uuid: Optional[str],
    ) -> dict[str, Any]:
        branch = self._download_repo_branch(job)
        project_name = self._cloudflare_project_name(job.cloudflare_project_name)
        download_repo_url = self._download_repo_url()
        download_url_base = self._download_repo_raw_base(job)
        raw_archive = self._raw_archive_runtime()
        description_parts = [
            f"Generate site data and build Astro site for release job {job.job_id}.",
            (
                f"Incrementally archive raw crawler data to {raw_archive.branch}."
                if raw_archive.enabled
                else "Raw crawler archive publication disabled for this run."
            ),
            (
                f"Publish partitioned CSV/JSON/XLSX downloads to {branch}."
                if job.include_git_push
                else "GitHub download publication disabled for this run."
            ),
            f"Pages: {project_name}" if job.include_cloudflare_deploy else "Cloudflare deploy disabled.",
        ]

        input_data = {
            "release_job_id": job.job_id,
            "release_job_name": job.name,
            "priority": job.priority,
            "auto_after_crawls": job.auto_after_crawls,
            "include_git_push": bool(job.include_git_push),
            "include_cloudflare_deploy": job.include_cloudflare_deploy,
            "require_clean_worktree": job.require_clean_worktree,
            "download_repo_branch": branch,
            "deployment_branch": job.github_branch,
            "github_repo_url": download_repo_url,
            "download_url_base": download_url_base,
            "cloudflare_project_name": project_name,
            "commit_message_template": job.commit_message_template,
            "timezone": job.timezone or self._config().timezone,
            "trigger": trigger,
            "manual_trigger": manual,
            "trigger_task_uuid": trigger_task_uuid,
            "generated_paths": list(GENERATED_DATA_PATHS),
            "raw_archive_enabled": raw_archive.enabled,
            "raw_archive_repo_url": raw_archive.repo_url,
            "raw_archive_branch": raw_archive.branch,
        }
        async with get_database() as db:
            # All release jobs share generated output directories. Locking the
            # complete job registry makes the active-task check and task insert
            # one cross-process critical section, not merely an in-process
            # asyncio lock. The task is born QUEUED with complete metadata, so a
            # worker cannot claim a half-initialized PENDING row.
            await db.execute(
                select(DataReleaseJob.id)
                .order_by(DataReleaseJob.id.asc())
                .with_for_update()
            )
            existing_tasks = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.task_type == TaskType.EXPORT_DATA,
                        Task.status.in_(
                            [
                                TaskStatus.PENDING,
                                TaskStatus.QUEUED,
                                TaskStatus.RUNNING,
                                TaskStatus.RETRYING,
                            ]
                        ),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            if existing_tasks:
                existing = existing_tasks[0]
                metadata = dict(existing.metadata_ or {})
                same_job = metadata.get("release_job_id") == job.job_id
                return {
                    "job_id": job.job_id,
                    "status": "skipped",
                    "task_uuid": existing.task_uuid,
                    "reason": "already_running" if same_job else "release_pipeline_busy",
                }

            task = Task(
                task_type=TaskType.EXPORT_DATA,
                task_name=f"Data Release: {job.name}",
                priority=self._normalize_priority(job.priority),
                description=" ".join(description_parts),
                input_data=input_data,
                status=TaskStatus.QUEUED,
                max_retries=self._automatic_retry_settings()[0],
                metadata_={
                    "release_job_id": job.job_id,
                    "release_job_name": job.name,
                    "trigger": trigger,
                    "manual_trigger": manual,
                    "trigger_task_uuid": trigger_task_uuid,
                    "automatic_retry": {
                        "policy_version": "release-transient-v1",
                        "status": "not_scheduled",
                        "scheduled_attempts": 0,
                    },
                },
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)

        await task_manager._broadcast(
            {
                "event": "task_status",
                "task_uuid": task.task_uuid,
                "status": TaskStatus.QUEUED.value,
                "progress": 0,
            }
        )
        return {
            "job_id": job.job_id,
            "status": "queued",
            "task_uuid": task.task_uuid,
        }

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        await self.ensure_storage()
        jobs = await self.load_jobs()
        jobs_payload: list[dict[str, Any]] = []
        for job in jobs:
            state = self._states.setdefault(job.job_id, DataReleaseJobState())
            self._sync_state_schedule(job, state, reset=False)
            task_status = None
            task_completed_at = None
            if state.last_task_uuid:
                task = await task_manager.get_task_by_uuid(state.last_task_uuid)
                if task is not None:
                    task_status = _coerce_status(task.status, state.last_status)
                    task_completed_at = task.completed_at or state.last_finished_at
            jobs_payload.append(
                {
                    **asdict(job),
                    "next_run_at": _iso(state.next_run_at),
                    "last_started_at": _iso(state.last_started_at),
                    "last_finished_at": _iso(task_completed_at or state.last_finished_at),
                    "last_status": task_status or state.last_status,
                    "last_error": state.last_error,
                    "last_task_uuid": state.last_task_uuid,
                    "run_count": state.run_count,
                    "skipped_count": state.skipped_count,
                }
            )
        return {
            "enabled": cfg.enabled,
            "timezone": cfg.timezone,
            "poll_interval_seconds": cfg.poll_interval_seconds,
            "auto_failure_cooldown_minutes": cfg.auto_failure_cooldown_minutes,
            "auto_retry_max_attempts": self._automatic_retry_settings()[0],
            "auto_retry_base_delay_seconds": self._automatic_retry_settings()[1],
            "auto_retry_max_delay_seconds": self._automatic_retry_settings()[2],
            "last_tick_at": _iso(self._last_tick_at),
            "jobs": jobs_payload,
        }

    async def integration_checks(self, job_id: str) -> dict[str, Any]:
        await self.ensure_storage()
        job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
        if job is None:
            raise ValueError(f"Data release job not found: {job_id}")

        branch = self._download_repo_branch(job)
        download_repo = await self._download_repo_check(job) if job.include_git_push else {
            "payload": {
                "repo_url": self._download_repo_url() or None,
                "branch": branch,
                "raw_base_url": self._download_repo_raw_base(job) or None,
                "read_access_ok": False,
                "write_access_ok": False,
                "read_check_output": "Skipped: direct download publication is disabled.",
                "write_check_output": "Skipped: direct download publication is disabled.",
                "ssh_transport": "disabled",
                "publisher_enabled": False,
            },
            "blockers": [],
        }
        worktree = await self._git_status_paths()
        tracked_generated_paths = await self._tracked_generated_paths()

        python_path = self._python_executable()
        situation_room_check = (
            await self._run_capture(
                [*self._update_situation_room_command(python_path=python_path), "--help"],
                cwd=ROOT_DIR,
                timeout=60,
            )
            if python_path.exists()
            else {"returncode": 127, "stdout": f"Python executable not found: {python_path}"}
        )
        wrangler_check = await self._run_capture(
            ["npm", "exec", "--", "wrangler", "--version"],
            cwd=ASTRO_DIR,
            timeout=60,
        )
        cloudflare = await self._cloudflare_check(job.cloudflare_project_name)
        raw_archive = await self._raw_archive_check()

        blockers: list[str] = []
        if job.include_git_push:
            blockers.extend(download_repo["blockers"])
        if job.include_cloudflare_deploy and not job.include_git_push:
            blockers.append(
                "Cloudflare deployment requires direct-download publication so the "
                "new site never references unpublished CSV/JSON/XLSX partitions."
            )
        if job.include_cloudflare_deploy:
            blockers.extend(cloudflare["blockers"])
        blockers.extend(raw_archive["blockers"])
        if not python_path.exists():
            blockers.append(f"Python executable not found: {python_path}")
        elif situation_room_check["returncode"] != 0:
            output = str(situation_room_check.get("stdout") or "").strip()
            detail = output.splitlines()[-1] if output else "command returned no diagnostic output"
            blockers.append(f"Situation Room refresh command is unavailable: {detail}")
        if job.include_cloudflare_deploy and wrangler_check["returncode"] != 0:
            blockers.append("Wrangler CLI is unavailable.")
        if job.include_cloudflare_deploy and not cloudflare["payload"].get("production_branch"):
            blockers.append("Cloudflare Pages project has no production branch configured.")
        if tracked_generated_paths:
            blockers.append(
                "Generated data is still tracked by the code repository: "
                + ", ".join(tracked_generated_paths)
            )

        return {
            "checked_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "overall_ready": not blockers,
            "blockers": blockers,
            "git": {
                "env_var": "GITHUB_DATA_SHARE_REPO_URL",
                "repo_url": download_repo["payload"]["repo_url"],
                "branch": branch,
                "raw_base_url": download_repo["payload"]["raw_base_url"],
                "read_access_ok": download_repo["payload"]["read_access_ok"],
                "write_access_ok": download_repo["payload"]["write_access_ok"],
                "read_check_output": download_repo["payload"]["read_check_output"],
                "write_check_output": download_repo["payload"]["write_check_output"],
                "ssh_transport": download_repo["payload"].get("ssh_transport"),
                "require_clean_worktree": job.require_clean_worktree,
                "dirty_blocking_paths": worktree,
            },
            "cloudflare": cloudflare["payload"],
            "raw_archive": raw_archive["payload"],
            "commands": {
                "python_path": str(python_path),
                "python_exists": python_path.exists(),
                "wrangler_available": wrangler_check["returncode"] == 0,
                "wrangler_version": wrangler_check["stdout"].strip() or None,
            },
            "repository_boundary": {
                "generated_paths": list(GENERATED_DATA_PATHS),
                "tracked_paths": tracked_generated_paths,
                "enforced": not tracked_generated_paths,
            },
        }

    async def execute_release_task(self, task: Task) -> dict[str, Any]:
        runtime = release_pipeline.ReleasePipelineRuntime(
            task_manager=task_manager,
            get_config=get_config,
            get_database=get_database,
            task_model=Task,
            root_dir=ROOT_DIR,
            astro_dir=ASTRO_DIR,
            download_repo_branch=DOWNLOAD_REPO_BRANCH,
            raw_archive_branch=self._raw_archive_runtime().branch,
            generate_timeout_seconds=GENERATE_SITE_DATA_TIMEOUT_SECONDS,
            astro_build_timeout_seconds=ASTRO_BUILD_TIMEOUT_SECONDS,
            download_publish_timeout_seconds=DOWNLOAD_PUBLISH_TIMEOUT_SECONDS,
            cloudflare_deploy_timeout_seconds=CLOUDFLARE_DEPLOY_TIMEOUT_SECONDS,
        )
        return await release_pipeline.execute_release_task(self, task, runtime=runtime)

    async def _release_identity_for_task(
        self,
        task: Task,
        deployment_branch: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Persist and reuse one release identity across retries of the same task."""

        generated = await self._site_release_identity(deployment_branch)
        async with get_database() as db:
            task_obj = (
                await db.execute(
                    select(Task).where(Task.id == task.id).with_for_update()
                )
            ).scalar_one_or_none()
            if task_obj is None:
                return generated, {}
            metadata = dict(task_obj.metadata_ or {})
            existing = dict(metadata.get("release_identity") or {})
            if (
                existing.get("source_commit") == generated.get("source_commit")
                and existing.get("deployment_branch") == generated.get("deployment_branch")
                and existing.get("release_id")
            ):
                identity = existing
            else:
                identity = generated
                metadata["release_identity"] = identity
                # A code/deployment-branch change is a new immutable attempt;
                # prior downstream checkpoints do not apply to it.
                metadata["release_checkpoints"] = {}
                task_obj.metadata_ = metadata
                await db.commit()
            return identity, dict(metadata.get("release_checkpoints") or {})

    async def _record_release_checkpoint(
        self,
        task_uuid: str,
        name: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Durably record a completed external side effect for retry recovery."""

        async with get_database() as db:
            task_obj = (
                await db.execute(
                    select(Task)
                    .where(Task.task_uuid == task_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if task_obj is None:
                return
            metadata = dict(task_obj.metadata_ or {})
            checkpoints = dict(metadata.get("release_checkpoints") or {})
            checkpoints[name] = {
                "completed_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                **dict(payload or {}),
            }
            metadata["release_checkpoints"] = checkpoints
            task_obj.metadata_ = metadata
            await db.commit()

    def _compute_next_run(
        self,
        job: DataReleaseJobConfig,
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
        return None

    def _normalize_priority(self, value: str) -> TaskPriority:
        normalized = (value or "high").strip().lower()
        try:
            return TaskPriority(normalized)
        except ValueError:
            return TaskPriority.HIGH

    def _python_executable(self) -> Path:
        venv_python = ROOT_DIR / "venv" / "bin" / "python3"
        return venv_python if venv_python.exists() else Path(sys.executable)

    def _render_commit_message(self, job: DataReleaseJobConfig, *, branch: str, tz: ZoneInfo) -> str:
        now = datetime.now(tz)
        template = job.commit_message_template or self._config().default_commit_message_template
        variables = {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "date": now.strftime("%Y-%m-%d"),
            "branch": branch,
            "job_id": job.job_id,
        }
        try:
            return template.format(**variables)
        except Exception:
            return self._config().default_commit_message_template.format(**variables)

    def _download_repo_url(self) -> str:
        return get_data_share_repo_url().strip()

    def _download_repo_branch(self, job: DataReleaseJobConfig) -> str:
        return self._normalize_download_repo_branch(job.github_branch)

    def _normalize_download_repo_branch(self, branch: Optional[str]) -> str:
        default_branch = get_data_share_repo_branch()
        candidate = (branch or "").strip()
        legacy_branch = system_settings_service.github_runtime()["default_github_branch"].strip() or self._current_git_branch_fallback()
        if not candidate:
            return default_branch
        if candidate == legacy_branch and default_branch != legacy_branch:
            return default_branch
        return candidate

    def _download_repo_raw_base(self, job: DataReleaseJobConfig) -> str:
        return get_data_share_raw_base_url(
            repo_url=self._download_repo_url(),
            branch=self._download_repo_branch(job),
        )

    def _cloudflare_project_name(self, project_name: Optional[str]) -> str:
        return self._normalize_cloudflare_project_name(project_name)

    def _normalize_cloudflare_project_name(self, project_name: Optional[str]) -> str:
        default_project_name = system_settings_service.cloudflare_runtime()["default_cloudflare_project_name"].strip() or "globalid"
        candidate = (project_name or "").strip()
        legacy_default_project_name = "globalid"
        if not candidate:
            return default_project_name
        if candidate == legacy_default_project_name and default_project_name != legacy_default_project_name:
            return default_project_name
        return candidate

    def _cloudflare_api_token(self) -> str:
        return system_settings_service.cloudflare_runtime()["cloudflare_api_token"].strip()

    def _cloudflare_account_id(self) -> str:
        return system_settings_service.cloudflare_runtime()["cloudflare_account_id"].strip()

    async def _current_git_branch(self) -> str:
        result = await self._run_capture(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT_DIR,
        )
        branch = result["stdout"].strip() if result["returncode"] == 0 else ""
        if branch and branch != "HEAD":
            return branch
        return self._current_git_branch_fallback()

    async def _git_head_full(self) -> str:
        result = await self._run_capture(["git", "rev-parse", "HEAD"], cwd=ROOT_DIR)
        if result["returncode"] != 0:
            return ""
        return result["stdout"].strip()

    async def _git_branch_commit(self, branch: str) -> str:
        """Resolve a local or origin branch without changing the working tree."""

        normalized = branch.strip()
        if not normalized:
            return ""
        for ref in (f"refs/remotes/origin/{normalized}", f"refs/heads/{normalized}"):
            result = await self._run_capture(
                ["git", "rev-parse", "--verify", ref],
                cwd=ROOT_DIR,
            )
            if result["returncode"] == 0 and result["stdout"].strip():
                return result["stdout"].strip()
        return ""

    async def _site_release_identity(self, deployment_branch: str) -> dict[str, Any]:
        source_commit = await self._git_head_full()
        source_branch = await self._current_git_branch()
        deployment_commit = await self._git_branch_commit(deployment_branch)
        if source_commit and deployment_commit == source_commit:
            source_branch = deployment_branch
        built_at = datetime.now(ZoneInfo("UTC")).isoformat()
        short_commit = source_commit[:12] if source_commit else "unknown"
        release_id = f"{datetime.now(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')}-{short_commit}"
        return {
            "release_id": release_id,
            "built_at": built_at,
            "source_branch": source_branch or "detached",
            "source_commit": source_commit or "unknown",
            "deployment_branch": deployment_branch,
            "commit_dirty": bool(await self._git_status_paths()),
        }

    def _write_site_release_manifest(self, identity: dict[str, Any]) -> dict[str, Any]:
        dist_dir = ASTRO_DIR / "dist"
        if not dist_dir.is_dir():
            raise RuntimeError(f"Astro build output is missing: {dist_dir}")

        assets_dir = dist_dir / "_astro"
        visual_modules = sorted(
            path.name
            for path in assets_dir.glob("*.js")
            if path.is_file() and path.name.startswith(SITE_VISUAL_MODULE_PREFIXES)
        )
        payload = {
            **identity,
            "visual_modules": visual_modules,
        }
        SITE_RELEASE_MANIFEST.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    def _cloudflare_deploy_command(
        self,
        *,
        project_name: str,
        branch: str,
        source_commit: str,
        commit_message: str,
        commit_dirty: bool,
    ) -> list[str]:
        return build_cloudflare_deploy_command(
            project_name=project_name,
            branch=branch,
            source_commit=source_commit,
            commit_message=commit_message,
            commit_dirty=commit_dirty,
        )

    def _generate_site_data_command(
        self,
        *,
        python_path: Path,
        download_url_base: str,
    ) -> list[str]:
        return build_generate_site_data_command(
            python_path=python_path,
            download_url_base=download_url_base,
        )

    def _update_situation_room_command(self, *, python_path: Path) -> list[str]:
        return build_update_situation_room_command(python_path=python_path)

    def _validate_situation_release_command(self, *, python_path: Path) -> list[str]:
        return build_validate_situation_release_command(python_path=python_path)

    def _publish_download_repo_command(
        self,
        *,
        python_path: Path,
        repo_url: str,
        commit_message: str,
        branch: str | None = None,
    ) -> list[str]:
        return build_publish_download_repo_command(
            python_path=python_path,
            source_dir=DIRECT_DOWNLOAD_DIR,
            repo_url=repo_url,
            commit_message=commit_message,
            branch=branch.strip() if branch and branch.strip() else get_data_share_repo_branch(),
        )

    def _raw_archive_runtime(self) -> SimpleNamespace:
        """Resolve raw-archive settings from the same control-center source as downloads."""

        cfg = get_config()
        archive = cfg.raw_archive
        github = system_settings_service.github_runtime()
        return SimpleNamespace(
            enabled=bool(github["raw_archive_enabled"]),
            repo_url=str(github["raw_archive_repo_url"] or "").strip(),
            branch=str(github["raw_archive_branch"] or "").strip() or "main",
            repository_dir=archive.repository_dir,
            git_timeout_seconds=archive.git_timeout_seconds,
        )

    def _publish_raw_archive_command(
        self, *, python_path: Path, archive: Any | None = None
    ) -> list[str]:
        archive = archive or self._raw_archive_runtime()
        cfg = get_config()
        return build_publish_raw_archive_command(
            python_path=python_path,
            source_dir=cfg.raw_data_dir,
            repository_dir=archive.repository_dir,
            repo_url=archive.repo_url,
            git_timeout_seconds=archive.git_timeout_seconds,
        )

    def _env_file_values(self) -> dict[str, str]:
        if dotenv_values is None or not ENV_PATH.exists():
            return {}
        values = dotenv_values(ENV_PATH)
        return {key: str(value) for key, value in values.items() if value is not None}

    def _env_value(self, name: str, default: str = "") -> str:
        return (os.getenv(name) or self._env_file_values().get(name) or default).strip()

    def _subscription_options_sync_plan(self) -> tuple[bool, str]:
        raw = self._env_value("SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE", "auto").lower()
        if raw in SUBSCRIPTION_SYNC_FALSE_VALUES:
            return False, "SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE is disabled."
        if not SUBSCRIPTIONS_SCRIPT.exists():
            return False, f"Subscription helper script is missing: {SUBSCRIPTIONS_SCRIPT}"

        missing = [
            name
            for name in ("SUBSCRIPTIONS__D1_DATABASE_NAME", "SUBSCRIPTIONS__D1_DATABASE_ID")
            if not self._env_value(name)
        ]
        target_environment = self._env_value("SUBSCRIPTIONS__REMOTE_ENVIRONMENT").lower()
        if target_environment not in {"staging", "production"}:
            missing.append("SUBSCRIPTIONS__REMOTE_ENVIRONMENT")
        if self._env_value("SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC") != target_environment:
            missing.append("SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC")
        if not self._cloudflare_api_token():
            missing.append("CLOUDFLARE_API_TOKEN")
        if missing and raw not in SUBSCRIPTION_SYNC_STRICT_VALUES:
            return False, "Subscription D1 sync is in auto mode and skipped because settings are missing: " + ", ".join(missing)

        return True, "Subscription D1 option sync is enabled."

    def _subscription_options_sync_is_strict(self) -> bool:
        raw = self._env_value("SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE", "auto").lower()
        return raw in SUBSCRIPTION_SYNC_STRICT_VALUES

    async def _sync_subscription_options_if_needed(self, task_uuid: str, *, job_id: str) -> bool:
        should_sync, sync_reason = self._subscription_options_sync_plan()
        metadata = {"event": "subscription_options_sync", "release_job_id": job_id}
        if not should_sync:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="info",
                title="Subscription Options Sync Skipped",
                content=sync_reason,
                content_type="text",
                metadata={**metadata, "event": "subscription_options_sync_skip"},
            )
            return False

        try:
            await self._run_logged_command(
                task_uuid,
                title="Sync Subscription Options",
                cmd=[
                    str(SUBSCRIPTIONS_SCRIPT),
                    "sync-options-remote",
                    self._env_value("SUBSCRIPTIONS__REMOTE_ENVIRONMENT").lower(),
                ],
                cwd=ROOT_DIR,
                env={
                    "CI": "1",
                    "CLOUDFLARE_API_TOKEN": self._cloudflare_api_token(),
                    "CLOUDFLARE_ACCOUNT_ID": self._cloudflare_account_id(),
                    "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC": self._env_value(
                        "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC"
                    ),
                },
                metadata=metadata,
                timeout_seconds=SUBSCRIPTION_SYNC_TIMEOUT_SECONDS,
            )
            return True
        except RuntimeError as exc:
            if self._subscription_options_sync_is_strict():
                raise
            message = (
                f"{sync_reason}\n\n"
                "Subscription option sync is running in auto mode, so this release will continue. "
                "Set SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE=true to make this step blocking.\n\n"
                f"{exc}"
            )
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="warning",
                title="Subscription Options Sync Failed",
                content=message,
                content_type="text",
                metadata={**metadata, "event": "subscription_options_sync_failed"},
            )
            logger.warning("Subscription options sync failed in auto mode; release will continue: %s", exc)
            return False

    def _is_github_ssh_repo_url(self, repo_url: str) -> bool:
        return release_checks.is_github_ssh_repo_url(repo_url, GITHUB_SSH_PREFIXES)

    def _build_git_env(
        self,
        repo_url: str,
        *,
        use_github_ssh_over_443: bool = False,
    ) -> dict[str, str]:
        return release_checks.build_git_env(
            repo_url,
            github_ssh_prefixes=GITHUB_SSH_PREFIXES,
            use_github_ssh_over_443=use_github_ssh_over_443,
        )

    async def _raw_archive_check(self) -> dict[str, Any]:
        config = get_config()
        archive = self._raw_archive_runtime()
        return await release_checks.raw_archive_check(
            enabled=archive.enabled,
            repo_url=archive.repo_url,
            source_dir=config.raw_data_dir,
            repository_dir=archive.repository_dir,
            branch=archive.branch,
            root_dir=ROOT_DIR,
            github_ssh_prefixes=GITHUB_SSH_PREFIXES,
            run_capture=self._run_capture,
        )

    async def _git_status_paths(self) -> list[str]:
        return await release_checks.git_status_paths(
            run_capture=self._run_capture,
            root_dir=ROOT_DIR,
        )

    async def _tracked_generated_paths(self) -> list[str]:
        return await release_checks.tracked_generated_paths(
            run_capture=self._run_capture,
            root_dir=ROOT_DIR,
            generated_data_paths=GENERATED_DATA_PATHS,
        )

    async def _download_repo_check(self, job: DataReleaseJobConfig) -> dict[str, Any]:
        return await release_checks.download_repo_check(
            repo_url=self._download_repo_url(),
            branch=self._download_repo_branch(job),
            raw_base_url=self._download_repo_raw_base(job),
            root_dir=ROOT_DIR,
            github_ssh_prefixes=GITHUB_SSH_PREFIXES,
            run_capture=self._run_capture,
        )

    async def _cloudflare_check(self, project_name: Optional[str]) -> dict[str, Any]:
        return await release_checks.cloudflare_check(
            project=self._cloudflare_project_name(project_name),
            token=self._cloudflare_api_token(),
            account_id=self._cloudflare_account_id(),
            api_json=self._cloudflare_api_json,
            latest_deployment=self._cloudflare_latest_deployment,
        )

    async def _cloudflare_api_json(self, url: str) -> dict[str, Any]:
        return await release_checks.cloudflare_api_json(url, token=self._cloudflare_api_token())

    def _normalize_cloudflare_deployment(self, deployment: dict[str, Any]) -> dict[str, Any]:
        return release_checks.normalize_cloudflare_deployment(deployment)

    async def _cloudflare_latest_deployment(
        self,
        project_name: str,
        *,
        environment: str,
    ) -> Optional[dict[str, Any]]:
        return await release_checks.cloudflare_latest_deployment(
            project_name,
            environment=environment,
            account_id=self._cloudflare_account_id(),
            api_json=self._cloudflare_api_json,
        )

    def _cloudflare_deployment_matches(
        self,
        deployment: Optional[dict[str, Any]],
        identity: dict[str, Any],
    ) -> bool:
        return release_checks.cloudflare_deployment_matches(deployment, identity)

    async def _public_json(self, url: str) -> dict[str, Any]:
        return await release_checks.public_json(url)

    async def _verify_cloudflare_production_release(
        self,
        *,
        project_name: str,
        subdomain: str,
        release_identity: dict[str, Any],
    ) -> dict[str, Any]:
        return await release_checks.verify_cloudflare_production_release(
            project_name=project_name,
            subdomain=subdomain,
            release_identity=release_identity,
            latest_deployment=self._cloudflare_latest_deployment,
            fetch_public_json=self._public_json,
        )

    async def _run_capture(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: Optional[dict[str, str]] = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        return await run_capture(
            cmd,
            cwd=cwd,
            env=env,
            timeout=timeout,
            terminate=self._terminate_process_tree,
        )

    async def _run_logged_command(
        self,
        task_uuid: str,
        *,
        title: str,
        cmd: list[str],
        cwd: Path,
        env: Optional[dict[str, str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        timeout_seconds: float = DEFAULT_LOGGED_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        await run_logged_command(
            task_uuid,
            title=title,
            cmd=cmd,
            cwd=cwd,
            task_manager=task_manager,
            logger=logger,
            terminate=self._terminate_process_tree,
            env=env,
            metadata=metadata,
            timeout_seconds=timeout_seconds,
            cancel_poll_seconds=LOGGED_COMMAND_CANCEL_POLL_SECONDS,
        )

    @staticmethod
    async def _terminate_process_tree(
        proc: asyncio.subprocess.Process,
        *,
        grace_seconds: float = 5,
    ) -> None:
        await terminate_process_tree(proc, grace_seconds=grace_seconds)

    def _current_git_branch_fallback(self) -> str:
        branch = os.getenv("DATA_RELEASE_GIT_BRANCH", "").strip()
        if branch:
            return branch
        head = ROOT_DIR / ".git" / "HEAD"
        try:
            content = head.read_text(encoding="utf-8").strip()
        except Exception:
            return "master"
        if content.startswith("ref: "):
            return content.rsplit("/", 1)[-1]
        return "master"


data_release_service = DataReleaseService()
