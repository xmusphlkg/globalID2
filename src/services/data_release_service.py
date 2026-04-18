"""Data release workflow orchestration for site export, Git push, and Pages deploy."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest
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
from src.services.exceptions import TaskCancelledError

logger = get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
ASTRO_DIR = ROOT_DIR / "astro-site"
RELEASE_PATHS = ("astro-site/src/data", "astro-site/dist")
AUTO_RELEASE_TASK_TYPES = (
    TaskType.CRAWL_DATA,
    TaskType.PROCESS_DATA,
    TaskType.GENERATE_REPORT,
)
GITHUB_SSH_PREFIXES = ("git@github.com:", "ssh://git@github.com/")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _coerce_status(value: Any, fallback: str = "idle") -> str:
    if hasattr(value, "value"):
        return str(value.value)
    if value is None:
        return fallback
    return str(value)


@dataclass
class DataReleaseJobConfig:
    job_id: str
    name: str
    enabled: bool = True
    priority: str = "high"
    auto_after_crawls: bool = True
    include_git_push: bool = True
    include_cloudflare_deploy: bool = True
    require_clean_worktree: bool = True
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
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
        if self._storage_ready:
            return
        async with self._storage_lock:
            if self._storage_ready:
                return
            from src.core.database import get_engine

            engine = get_engine()
            async with engine.begin() as conn:
                await conn.run_sync(DataReleaseJob.__table__.create, checkfirst=True)
            await self._seed_default_job_if_needed()
            self._storage_ready = True

    async def _seed_default_job_if_needed(self) -> None:
        cfg = self._config()
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
                    auto_after_crawls=True,
                    include_git_push=True,
                    include_cloudflare_deploy=True,
                    require_clean_worktree=True,
                    daily_time=None,
                    interval_minutes=None,
                    timezone=cfg.timezone,
                    github_remote=cfg.default_github_remote,
                    github_branch=get_data_share_repo_branch(),
                    cloudflare_project_name=cfg.default_cloudflare_project_name,
                    commit_message_template=cfg.default_commit_message_template,
                    notes="Default release pipeline for generated site data and Cloudflare Pages deploy.",
                )
            )
            await db.commit()

    async def load_jobs(self) -> list[DataReleaseJobConfig]:
        await self.ensure_storage()
        cfg = self._config()
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
                    github_remote=row.github_remote or cfg.default_github_remote,
                    github_branch=resolved_branch,
                    cloudflare_project_name=resolved_project_name,
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

    async def remove_job_state(self, job_id: str) -> None:
        async with self._lock:
            self._states.pop(job_id, None)

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.enabled:
            logger.info("Data release scheduler disabled by configuration")
            return
        await self.ensure_storage()
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        for job in await self.load_jobs():
            state = self._states.setdefault(job.job_id, DataReleaseJobState())
            self._sync_state_schedule(job, state, reset=state.next_run_at is None)
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
            self._last_tick_at = datetime.utcnow()
            try:
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
            await asyncio.sleep(cfg.poll_interval_seconds)

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
                    .where(Task.task_type == TaskType.EXPORT_DATA)
                    .order_by(Task.created_at.desc())
                )
            ).scalars().all()
        for task in tasks:
            metadata = dict(task.metadata_ or {})
            if metadata.get("release_job_id") != job_id:
                continue
            task_time = task.completed_at or task.created_at
            return bool(task_time and task_time >= crawl_completed_at)
        return False

    async def _enqueue_release_task(
        self,
        job: DataReleaseJobConfig,
        *,
        manual: bool,
        trigger: str,
        trigger_task_uuid: Optional[str],
    ) -> dict[str, Any]:
        async with get_database() as db:
            existing_tasks = (
                await db.execute(
                    select(Task).where(
                        Task.task_type == TaskType.EXPORT_DATA,
                        Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING]),
                    )
                )
            ).scalars().all()
            for existing in existing_tasks:
                metadata = dict(existing.metadata_ or {})
                if metadata.get("release_job_id") == job.job_id:
                    return {
                        "job_id": job.job_id,
                        "status": "skipped",
                        "task_uuid": existing.task_uuid,
                        "reason": "already_running",
                    }

        branch = self._download_repo_branch(job)
        project_name = self._cloudflare_project_name(job.cloudflare_project_name)
        download_repo_url = self._download_repo_url()
        download_url_base = self._download_repo_raw_base(job)
        description_parts = [
            f"Generate site data and build Astro site for release job {job.job_id}.",
            (
                f"Download repo: {download_repo_url} ({branch})"
                if job.include_git_push and download_repo_url
                else "Download repo publish disabled."
            ),
            f"Pages: {project_name}" if job.include_cloudflare_deploy else "Cloudflare deploy disabled.",
        ]

        input_data = {
            "release_job_id": job.job_id,
            "release_job_name": job.name,
            "priority": job.priority,
            "auto_after_crawls": job.auto_after_crawls,
            "include_git_push": job.include_git_push,
            "include_cloudflare_deploy": job.include_cloudflare_deploy,
            "require_clean_worktree": job.require_clean_worktree,
            "github_branch": branch,
            "github_repo_url": download_repo_url,
            "download_url_base": download_url_base,
            "cloudflare_project_name": project_name,
            "commit_message_template": job.commit_message_template,
            "timezone": job.timezone or self._config().timezone,
            "trigger": trigger,
            "manual_trigger": manual,
            "trigger_task_uuid": trigger_task_uuid,
            "release_paths": list(RELEASE_PATHS),
        }
        task = await task_manager.create_task(
            task_type=TaskType.EXPORT_DATA,
            task_name=f"Data Release: {job.name}",
            priority=self._normalize_priority(job.priority),
            description=" ".join(description_parts),
            input_data=input_data,
        )
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                metadata = dict(task_obj.metadata_ or {})
                metadata.update(
                    {
                        "release_job_id": job.job_id,
                        "release_job_name": job.name,
                        "trigger": trigger,
                        "manual_trigger": manual,
                        "trigger_task_uuid": trigger_task_uuid,
                    }
                )
                task_obj.metadata_ = metadata
                await db.commit()

        task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
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
            "last_tick_at": _iso(self._last_tick_at),
            "jobs": jobs_payload,
        }

    async def integration_checks(self, job_id: str) -> dict[str, Any]:
        await self.ensure_storage()
        job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
        if job is None:
            raise ValueError(f"Data release job not found: {job_id}")

        branch = self._download_repo_branch(job)
        download_repo = await self._download_repo_check(job)
        worktree = await self._git_status_paths()
        blocking_dirty = [
            path for path in worktree
            if not self._is_release_path(path)
        ]
        release_dirty = [path for path in worktree if self._is_release_path(path)]

        python_path = self._python_executable()
        wrangler_check = await self._run_capture(
            ["npm", "exec", "--", "wrangler", "--version"],
            cwd=ASTRO_DIR,
            timeout=60,
        )
        cloudflare = await self._cloudflare_check(job.cloudflare_project_name)

        blockers: list[str] = []
        if job.include_git_push:
            blockers.extend(download_repo["blockers"])
        if job.include_cloudflare_deploy:
            blockers.extend(cloudflare["blockers"])
        if not python_path.exists():
            blockers.append(f"Python executable not found: {python_path}")
        if job.include_cloudflare_deploy and wrangler_check["returncode"] != 0:
            blockers.append("Wrangler CLI is unavailable.")

        return {
            "checked_at": datetime.utcnow().isoformat(),
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
                "dirty_release_paths": release_dirty,
                "dirty_blocking_paths": blocking_dirty,
            },
            "cloudflare": cloudflare["payload"],
            "commands": {
                "python_path": str(python_path),
                "python_exists": python_path.exists(),
                "wrangler_available": wrangler_check["returncode"] == 0,
                "wrangler_version": wrangler_check["stdout"].strip() or None,
            },
        }

    async def execute_release_task(self, task: Task) -> dict[str, Any]:
        input_data = dict(task.input_data or {})
        job_id = str(input_data.get("release_job_id") or "").strip()
        if not job_id:
            raise RuntimeError("Release task is missing release_job_id")
        job = next((item for item in await self.load_jobs() if item.job_id == job_id), None)
        if job is None:
            raise RuntimeError(f"Release job not found: {job_id}")

        checks = await self.integration_checks(job.job_id)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Release Preflight",
            content=json.dumps(checks, ensure_ascii=False, indent=2),
            content_type="json",
            metadata={"event": "release_preflight", "release_job_id": job.job_id},
        )
        if not checks["overall_ready"]:
            raise RuntimeError("Release preflight failed: " + "; ".join(checks["blockers"]))

        tz = ZoneInfo(job.timezone or self._config().timezone)
        branch = self._download_repo_branch(job)
        project_name = self._cloudflare_project_name(job.cloudflare_project_name)
        download_repo_url = self._download_repo_url()
        git_transport = str((checks.get("git") or {}).get("ssh_transport") or "default")
        git_env = self._build_git_env(
            download_repo_url,
            use_github_ssh_over_443=(git_transport == "github-ssh-over-443"),
        )
        download_url_base = self._download_repo_raw_base(job)
        publish_commit_message = self._render_commit_message(job, branch=branch, tz=tz)
        python_path = self._python_executable()

        await task_manager.update_task_progress(task.task_uuid, 5)
        generate_cmd = [
            str(python_path),
            "scripts/generate_site_data.py",
            "--download-url-base",
            download_url_base,
        ]
        if job.include_git_push:
            generate_cmd.extend(
                [
                    "--publish-downloads",
                    "--download-repo-url",
                    download_repo_url,
                    "--download-repo-branch",
                    branch,
                    "--download-commit-message",
                    publish_commit_message,
                ]
            )
        await self._run_logged_command(
            task.task_uuid,
            title="Generate Site Data",
            cmd=generate_cmd,
            cwd=ROOT_DIR,
            env=git_env,
            metadata={"event": "generate_site_data", "release_job_id": job.job_id},
        )
        await task_manager.update_task_progress(task.task_uuid, 35)

        await self._run_logged_command(
            task.task_uuid,
            title="Build Astro Site",
            cmd=["npm", "run", "build"],
            cwd=ASTRO_DIR,
            env={
                "GLOBALID_SKIP_SITE_DATA_GENERATION": "1",
            },
            metadata={"event": "astro_build", "release_job_id": job.job_id},
        )
        await task_manager.update_task_progress(task.task_uuid, 60)

        changed_release_paths = await self._git_dirty_release_paths()
        download_repo_published = bool(job.include_git_push)
        pages_deployed = False

        if changed_release_paths:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Release Diff Summary",
                content="\n".join(changed_release_paths),
                content_type="text",
                metadata={"event": "release_diff", "release_job_id": job.job_id},
            )
        else:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="warning",
                title="No Release Artifacts Changed",
                content="generate_site_data and Astro build completed, but no tracked release files changed under astro-site/src/data or astro-site/dist.",
                content_type="text",
                metadata={"event": "release_diff", "release_job_id": job.job_id},
            )

        if not job.include_git_push:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Download Repo Publish Skipped",
                content="Download-data repo publishing is disabled for this release job.",
                content_type="text",
                metadata={"event": "git_push_skip", "release_job_id": job.job_id},
            )

        await task_manager.update_task_progress(task.task_uuid, 88)

        if job.include_cloudflare_deploy:
            await self._run_logged_command(
                task.task_uuid,
                title="Deploy Cloudflare Pages",
                cmd=[
                    "npm",
                    "exec",
                    "--",
                    "wrangler",
                    "pages",
                    "deploy",
                    "dist",
                    "--project-name",
                    project_name,
                    "--commit-dirty=true",
                ],
                cwd=ASTRO_DIR,
                env={
                    "CI": "1",
                    "CLOUDFLARE_API_TOKEN": self._cloudflare_api_token(),
                    "CLOUDFLARE_ACCOUNT_ID": self._cloudflare_account_id(),
                },
                metadata={"event": "cloudflare_pages_deploy", "release_job_id": job.job_id},
            )
            pages_deployed = True
        else:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Cloudflare Deploy Skipped",
                content="Cloudflare Pages deployment is disabled for this release job.",
                content_type="text",
                metadata={"event": "cloudflare_skip", "release_job_id": job.job_id},
            )

        await task_manager.update_task_progress(task.task_uuid, 100)

        output = {
            "release_job_id": job.job_id,
            "release_job_name": job.name,
            "github_repo_url": download_repo_url,
            "github_branch": branch,
            "download_url_base": download_url_base,
            "cloudflare_project_name": project_name,
            "commit_message": publish_commit_message,
            "git_pushed": download_repo_published,
            "pages_deployed": pages_deployed,
            "changed_release_paths": changed_release_paths,
            "preflight": checks,
        }

        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = output
                await db.commit()

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Data Release Completed",
            content=(
                f"Release job: {job.name}\n"
                f"Download repo published: {'yes' if download_repo_published else 'no'}\n"
                f"Cloudflare deployed: {'yes' if pages_deployed else 'no'}\n"
                f"Download repo: {download_repo_url or '-'}"
            ),
            content_type="text",
            metadata={"event": "release_completed", "release_job_id": job.job_id},
        )
        return output

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
        legacy_branch = self._config().default_github_branch.strip() or self._current_git_branch_fallback()
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
        default_project_name = self._config().default_cloudflare_project_name.strip() or "globalid"
        candidate = (project_name or "").strip()
        legacy_default_project_name = "globalid"
        if not candidate:
            return default_project_name
        if candidate == legacy_default_project_name and default_project_name != legacy_default_project_name:
            return default_project_name
        return candidate

    def _cloudflare_api_token(self) -> str:
        return get_config().cloudflare_api_token.strip()

    def _cloudflare_account_id(self) -> str:
        return get_config().cloudflare_account_id.strip()

    def _is_github_ssh_repo_url(self, repo_url: str) -> bool:
        normalized = (repo_url or "").strip().lower()
        return any(normalized.startswith(prefix) for prefix in GITHUB_SSH_PREFIXES)

    def _build_git_env(
        self,
        repo_url: str,
        *,
        use_github_ssh_over_443: bool = False,
    ) -> dict[str, str]:
        ssh_parts = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if use_github_ssh_over_443 and self._is_github_ssh_repo_url(repo_url):
            # GitHub supports SSH over 443 via ssh.github.com, which helps in restricted networks.
            ssh_parts.extend(["-o", "Hostname=ssh.github.com", "-p", "443"])
        return {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": " ".join(ssh_parts),
        }

    async def _git_status_paths(self) -> list[str]:
        result = await self._run_capture(
            ["git", "status", "--porcelain=v1", "-uall"],
            cwd=ROOT_DIR,
        )
        if result["returncode"] != 0:
            return []
        paths: list[str] = []
        for raw_line in result["stdout"].splitlines():
            line = raw_line.rstrip()
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip().strip('"')
            if path:
                paths.append(path.replace("\\", "/"))
        return paths

    async def _git_dirty_release_paths(self) -> list[str]:
        return [path for path in await self._git_status_paths() if self._is_release_path(path)]

    def _is_release_path(self, path: str) -> bool:
        normalized = path.strip().lstrip("./").replace("\\", "/")
        return any(
            normalized == base or normalized.startswith(f"{base}/")
            for base in RELEASE_PATHS
        )

    async def _download_repo_check(self, job: DataReleaseJobConfig) -> dict[str, Any]:
        repo_url = self._download_repo_url()
        branch = self._download_repo_branch(job)
        raw_base_url = self._download_repo_raw_base(job)
        git_env = self._build_git_env(repo_url)
        ssh_transport = "default"
        payload = {
            "repo_url": repo_url or None,
            "branch": branch,
            "raw_base_url": raw_base_url or None,
            "read_access_ok": False,
            "write_access_ok": False,
            "read_check_output": None,
            "write_check_output": None,
            "ssh_transport": ssh_transport,
        }
        blockers: list[str] = []

        if not repo_url:
            blockers.append("Missing GITHUB_DATA_SHARE_REPO_URL.")
            return {"payload": payload, "blockers": blockers}

        read_check: dict[str, Any] = {"returncode": 127, "stdout": "read check did not run"}
        fallback_read_output: Optional[str] = None
        for attempt in range(1, 4):
            read_check = await self._run_capture(
                ["git", "ls-remote", repo_url, "HEAD"],
                cwd=ROOT_DIR,
                env=git_env,
                timeout=40,
            )

            if read_check["returncode"] != 0 and self._is_github_ssh_repo_url(repo_url):
                fallback_env = self._build_git_env(repo_url, use_github_ssh_over_443=True)
                fallback_read = await self._run_capture(
                    ["git", "ls-remote", repo_url, "HEAD"],
                    cwd=ROOT_DIR,
                    env=fallback_env,
                    timeout=40,
                )
                if fallback_read["returncode"] == 0:
                    read_check = fallback_read
                    git_env = fallback_env
                    ssh_transport = "github-ssh-over-443"
                    fallback_read_output = None
                else:
                    fallback_read_output = fallback_read.get("stdout") or ""

            if read_check["returncode"] == 0:
                break

            if attempt < 3:
                await asyncio.sleep(min(5, attempt * 2))

        if read_check["returncode"] != 0 and self._is_github_ssh_repo_url(repo_url) and fallback_read_output is not None:
            read_check["stdout"] = (
                "Primary SSH (port 22) failed:\n"
                + (read_check.get("stdout") or "")
                + "\n\nSSH-over-443 fallback failed:\n"
                + fallback_read_output
            ).strip()

        payload["read_check_output"] = read_check["stdout"]
        payload["read_access_ok"] = read_check["returncode"] == 0
        payload["ssh_transport"] = ssh_transport
        if read_check["returncode"] != 0:
            blockers.append("Download-data repo read check failed.")
            return {"payload": payload, "blockers": blockers}

        with tempfile.TemporaryDirectory(prefix="globalid-data-release-check-") as temp_dir:
            temp_path = Path(temp_dir)
            init_check = await self._run_capture(["git", "init"], cwd=temp_path, env=git_env, timeout=40)
            if init_check["returncode"] != 0:
                payload["write_check_output"] = init_check["stdout"]
                blockers.append("Download-data repo write check failed.")
                return {"payload": payload, "blockers": blockers}

            remote_add_check = await self._run_capture(
                ["git", "remote", "add", "origin", repo_url],
                cwd=temp_path,
                env=git_env,
                timeout=40,
            )
            if remote_add_check["returncode"] != 0:
                payload["write_check_output"] = remote_add_check["stdout"]
                blockers.append("Download-data repo write check failed.")
                return {"payload": payload, "blockers": blockers}

            commit_check = await self._run_capture(
                [
                    "git",
                    "-c",
                    "user.name=GlobalID Data Release",
                    "-c",
                    "user.email=noreply@globalid.local",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "chore: permission check",
                ],
                cwd=temp_path,
                env=git_env,
                timeout=40,
            )
            if commit_check["returncode"] != 0:
                payload["write_check_output"] = commit_check["stdout"]
                blockers.append("Download-data repo write check failed.")
                return {"payload": payload, "blockers": blockers}

            probe_branch = f"__globalid_write_probe__/{branch or 'main'}"
            write_check: dict[str, Any] = {"returncode": 127, "stdout": "write check did not run"}
            for attempt in range(1, 3):
                write_check = await self._run_capture(
                    ["git", "push", "--dry-run", "origin", f"HEAD:refs/heads/{probe_branch}"],
                    cwd=temp_path,
                    env=git_env,
                    timeout=40,
                )
                if write_check["returncode"] == 0:
                    break

                if self._is_github_ssh_repo_url(repo_url) and ssh_transport == "default":
                    fallback_env = self._build_git_env(repo_url, use_github_ssh_over_443=True)
                    fallback_write = await self._run_capture(
                        ["git", "push", "--dry-run", "origin", f"HEAD:refs/heads/{probe_branch}"],
                        cwd=temp_path,
                        env=fallback_env,
                        timeout=40,
                    )
                    if fallback_write["returncode"] == 0:
                        write_check = fallback_write
                        git_env = fallback_env
                        ssh_transport = "github-ssh-over-443"
                        break
                    write_check["stdout"] = (
                        "Primary SSH (port 22) failed:\n"
                        + (write_check.get("stdout") or "")
                        + "\n\nSSH-over-443 fallback failed:\n"
                        + (fallback_write.get("stdout") or "")
                    ).strip()

                if attempt < 2:
                    await asyncio.sleep(min(5, attempt * 2))

            payload["write_check_output"] = write_check["stdout"]
            payload["write_access_ok"] = write_check["returncode"] == 0
            payload["ssh_transport"] = ssh_transport
            if write_check["returncode"] != 0:
                blockers.append("Download-data repo write check failed.")

        return {"payload": payload, "blockers": blockers}

    async def _cloudflare_check(self, project_name: Optional[str]) -> dict[str, Any]:
        project = self._cloudflare_project_name(project_name)
        token = self._cloudflare_api_token()
        account_id = self._cloudflare_account_id()
        payload = {
            "project_name": project,
            "token_present": bool(token),
            "account_id_present": bool(account_id),
            "project_access_ok": False,
            "error": None,
        }
        blockers: list[str] = []
        if not token:
            blockers.append("Missing CLOUDFLARE_API_TOKEN.")
        if not account_id:
            blockers.append("Missing CLOUDFLARE_ACCOUNT_ID.")
        if not project:
            blockers.append("Missing Cloudflare Pages project name.")
        if blockers:
            payload["error"] = "; ".join(blockers)
            return {"payload": payload, "blockers": blockers}

        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/{project}"
        req = urlrequest.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                with urlrequest.urlopen(req, timeout=20) as response:
                    body = json.loads(response.read().decode("utf-8", errors="replace"))
                payload["project_access_ok"] = bool(body.get("success"))
                if not payload["project_access_ok"]:
                    messages = body.get("errors") or body.get("messages") or []
                    payload["error"] = json.dumps(messages, ensure_ascii=False)
                    blockers.append("Cloudflare Pages project check failed.")
                return {"payload": payload, "blockers": blockers}
            except urlerror.HTTPError as exc:
                last_exc = exc
                # 4xx (except 429) is usually credential/scope/config mismatch.
                if 400 <= exc.code < 500 and exc.code != 429:
                    payload["error"] = str(exc)
                    blockers.append("Cloudflare Pages project check failed.")
                    return {"payload": payload, "blockers": blockers}
            except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc

            if attempt < 3:
                await asyncio.sleep(min(5, attempt * 2))

        payload["error"] = str(last_exc) if last_exc else "Cloudflare check failed without exception details"
        blockers.append("Cloudflare Pages project is not reachable with current credentials.")
        return {"payload": payload, "blockers": blockers}

    async def _run_capture(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: Optional[dict[str, str]] = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=merged_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            return {
                "returncode": 127,
                "stdout": str(exc),
            }
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "returncode": 124,
                "stdout": f"Command timed out after {timeout}s: {' '.join(shlex.quote(part) for part in cmd)}",
            }
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
        }

    async def _run_logged_command(
        self,
        task_uuid: str,
        *,
        title: str,
        cmd: list[str],
        cwd: Path,
        env: Optional[dict[str, str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        metadata = dict(metadata or {})
        metadata["command"] = " ".join(shlex.quote(part) for part in cmd)
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title=f"{title} Started",
            content=f"$ {' '.join(shlex.quote(part) for part in cmd)}\nCWD: {cwd}",
            content_type="text",
            metadata=metadata,
        )

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=merged_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{title} failed to start: {exc}") from exc

        chunk_lines: list[str] = []
        chunk_index = 1
        output_tail: list[str] = []

        async def flush_chunk(force: bool = False) -> None:
            nonlocal chunk_lines, chunk_index
            if not chunk_lines and not force:
                return
            if chunk_lines:
                await task_manager.add_workbook_entry(
                    task_uuid,
                    entry_type="info",
                    title=f"{title} Output #{chunk_index}",
                    content="\n".join(chunk_lines),
                    content_type="text",
                    metadata={**metadata, "chunk": chunk_index, "event": metadata.get("event", "command_output")},
                )
                chunk_lines = []
                chunk_index += 1

        while True:
            if await task_manager.is_cancel_requested(task_uuid):
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                raise TaskCancelledError(f"Cancellation requested while running: {title}")

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=1)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            chunk_lines.append(text)
            output_tail.append(text)
            if len(output_tail) > 120:
                output_tail = output_tail[-120:]
            if len(chunk_lines) >= 40 or sum(len(item) for item in chunk_lines) >= 4000:
                await flush_chunk()

        returncode = await proc.wait()
        await flush_chunk(force=True)

        if returncode != 0:
            raise RuntimeError(
                f"{title} failed with exit code {returncode}.\n" + "\n".join(output_tail[-40:])
            )

        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="success",
            title=f"{title} Completed",
            content="\n".join(output_tail[-20:]) if output_tail else "Command completed successfully.",
            content_type="text",
            metadata=metadata,
        )

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
