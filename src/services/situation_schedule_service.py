"""Scheduled Situation refresh, run like Research Radar's sync.

Every ``interval_minutes`` the scheduler queues one low-priority
``REFRESH_SITUATION_SOURCES`` task: collect official reports, store them, fetch
full text and run peer-reviewed alert extraction.  The task only writes the
database; the data release reads it back and exports.  While reports are still
waiting to be read, the next run is pulled forward to ``catch_up_interval_minutes``
so a backlog drains in the background.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from src.control_plane.schedule_state import schedule_state_repository
from src.core import get_config, get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import SituationDocument, SituationNotice
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType

logger = get_logger(__name__)

_ACTIVE = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


@dataclass
class SituationScheduleState:
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str = "idle"
    last_error: str | None = None
    last_task_uuid: str | None = None


class SituationScheduleService:
    KIND = "situation"
    JOB_ID = "situation-refresh"

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._state = SituationScheduleState()

    def _config(self):
        return get_config().situation

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self._config().timezone))

    async def _load_state(self) -> None:
        persisted = (await schedule_state_repository.load(self.KIND)).get(self.JOB_ID, {})
        for field, value in persisted.items():
            setattr(self._state, field, value)

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.schedule_enabled:
            logger.info("Situation scheduler disabled by configuration")
            return
        await self._load_state()
        if self._state.next_run_at is None:
            self._state.next_run_at = self._now() + timedelta(minutes=cfg.catch_up_interval_minutes)
        await schedule_state_repository.save(self.KIND, self.JOB_ID, self._state)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="globalid-situation-scheduler")
        logger.info("Situation scheduler started")

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

    async def _active_task(self) -> Task | None:
        async with get_database() as db:
            return (
                await db.execute(
                    select(Task)
                    .where(Task.task_type == TaskType.REFRESH_SITUATION_SOURCES, Task.status.in_(_ACTIVE))
                    .order_by(Task.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def backlog(self) -> int:
        """Reports with a ready document that the models have not read yet."""

        async with get_database() as db:
            return int(
                (
                    await db.execute(
                        select(func.count(SituationNotice.id))
                        .join(SituationDocument, SituationDocument.notice_id == SituationNotice.notice_id)
                        .where(
                            SituationNotice.ai_status.in_(("pending", "failed")),
                            SituationDocument.status.in_(("ready", "pending", "failed")),
                        )
                    )
                ).scalar_one()
                or 0
            )

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        cfg = self._config()
        while not self._stop_event.is_set():
            try:
                now = self._now()
                due = self._state.next_run_at is None or now >= self._state.next_run_at
                if not due and await self._active_task() is None:
                    # Drain a backlog sooner than the normal cadence.
                    soon = now + timedelta(minutes=cfg.catch_up_interval_minutes)
                    if self._state.next_run_at > soon and await self.backlog() > 0:
                        self._state.next_run_at = soon
                        await schedule_state_repository.save(self.KIND, self.JOB_ID, self._state)
                if due:
                    await self.trigger_job(self.JOB_ID, manual=False)
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("Situation scheduled refresh failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=cfg.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def trigger_job(self, job_id: str, *, manual: bool) -> dict[str, Any]:
        if job_id != self.JOB_ID:
            raise ValueError(f"Situation job not found: {job_id}")
        cfg = self._config()
        async with self._lock:
            active = await self._active_task()
            if active is not None:
                self._state.next_run_at = self._now() + timedelta(minutes=cfg.catch_up_interval_minutes)
                await schedule_state_repository.save(self.KIND, self.JOB_ID, self._state)
                return {"job_id": job_id, "status": "skipped", "task_uuid": active.task_uuid, "reason": "A refresh is already active"}
            now = self._now()
            self._state.last_started_at = now
            try:
                task = await task_manager.create_task(
                    task_type=TaskType.REFRESH_SITUATION_SOURCES,
                    task_name="Situation: collect reports and extract alerts",
                    # Low priority: it runs when the worker is otherwise idle.
                    priority=TaskPriority.NORMAL if manual else TaskPriority.LOW,
                    status=TaskStatus.QUEUED,
                    description="Scheduled Situation refresh; writes the database only",
                    input_data={
                        "situation_job_id": self.JOB_ID,
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                        "fetch": True,
                        "export": False,
                        "document_limit": cfg.document_limit,
                        "ai_limit": cfg.ai_limit,
                    },
                    tags=["situation"],
                )
                self._state.last_task_uuid = task.task_uuid
                self._state.last_status = "queued"
                self._state.last_error = None
                return {"job_id": job_id, "status": "queued", "task_uuid": task.task_uuid}
            except Exception as exc:
                self._state.last_status = "failed"
                self._state.last_error = str(exc)
                raise
            finally:
                self._state.last_finished_at = self._now()
                self._state.next_run_at = self._state.last_finished_at + timedelta(minutes=cfg.interval_minutes)
                await schedule_state_repository.save(self.KIND, self.JOB_ID, self._state)

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        await self._load_state()
        async with get_database() as db:
            latest = (
                await db.execute(
                    select(Task)
                    .where(Task.task_type == TaskType.REFRESH_SITUATION_SOURCES)
                    .order_by(Task.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest is not None:
            self._state.last_task_uuid = latest.task_uuid
            self._state.last_status = str(_value(latest.status))
            self._state.last_started_at = latest.started_at or self._state.last_started_at
            self._state.last_finished_at = latest.completed_at or self._state.last_finished_at
            self._state.last_error = latest.last_error
        return {
            "timezone": cfg.timezone,
            "jobs": [
                {
                    "job_id": self.JOB_ID,
                    "name": "Situation Refresh",
                    "enabled": cfg.schedule_enabled,
                    "timezone": cfg.timezone,
                    "interval_minutes": cfg.interval_minutes,
                    "daily_time": None,
                    "next_run_at": self._state.next_run_at,
                    "last_started_at": self._state.last_started_at,
                    "last_finished_at": self._state.last_finished_at,
                    "last_status": self._state.last_status,
                    "last_error": self._state.last_error,
                    "last_task_uuid": self._state.last_task_uuid,
                    "catch_up_interval_minutes": cfg.catch_up_interval_minutes,
                    "document_limit": cfg.document_limit,
                    "ai_limit": cfg.ai_limit,
                    "backlog": await self.backlog(),
                }
            ],
        }


situation_schedule_service = SituationScheduleService()

__all__ = ["SituationScheduleService", "situation_schedule_service"]
