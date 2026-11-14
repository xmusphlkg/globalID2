"""Scheduled Situation sampling, queued like surveillance ingestion.

Each registered source is one ``REFRESH_SITUATION_SOURCES`` task every
``interval_minutes``. The scheduler only enqueues. The task worker runs the
sample on the general lane (``skip_ai``) and a separate reading task on the AI
lane when stored reports are still waiting. Sources that share an upstream are
given different minutes in the cycle, and only ``sample_pool_size`` samples
may be queued or running together.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from src.control_plane.schedule_state import schedule_state_repository
from src.core import get_config, get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import SituationDocument, SituationNotice
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType
from src.services.global_sources.catalogue import SOURCE_REGISTRY
from src.services.situation_notices.source_control import covers, task_sources

logger = get_logger(__name__)

_ACTIVE = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)
SAMPLE_PREFIX = "situation-sample-"
READ_JOB_ID = "situation-read"


def source_upstream(source_id: str) -> str:
    """Host that a feed calls, so two listings on one site stay apart."""

    endpoint = str(SOURCE_REGISTRY[source_id].get("endpoint") or "")
    host = urlparse(endpoint).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or source_id


def source_label(source_id: str) -> str:
    label = SOURCE_REGISTRY[source_id].get("label") or {}
    if isinstance(label, dict):
        return str(label.get("en") or source_id)
    return str(label or source_id)


def sample_job_id(source_id: str) -> str:
    return f"{SAMPLE_PREFIX}{source_id}"


def source_from_job(job_id: str) -> str | None:
    if not job_id.startswith(SAMPLE_PREFIX):
        return None
    source_id = job_id[len(SAMPLE_PREFIX):]
    return source_id if source_id in SOURCE_REGISTRY else None


def assign_source_phases(source_ids: list[str], *, interval: int) -> dict[str, int]:
    """Spread feeds across the cycle and keep one upstream off the same minute."""

    upstreams = {source_id: source_upstream(source_id) for source_id in source_ids}
    sizes = Counter(upstreams.values())
    ordered = sorted(source_ids, key=lambda source_id: (-sizes[upstreams[source_id]], upstreams[source_id], source_id))
    counts = [0] * interval
    upstream_counts = [Counter() for _ in range(interval)]
    used: dict[str, list[int]] = {}
    phases: dict[str, int] = {}
    for source_id in ordered:
        upstream = upstreams[source_id]
        preferred = int.from_bytes(hashlib.sha256(source_id.encode()).digest()[:4], "big") % interval

        def score(slot: int, upstream: str = upstream, preferred: int = preferred) -> tuple[int, int, int, int]:
            distances = [
                min((slot - other) % interval, (other - slot) % interval)
                for other in used.get(upstream, [])
            ]
            return (
                upstream_counts[slot][upstream],
                max(0, 10 - min(distances, default=10)),
                counts[slot],
                (slot - preferred) % interval,
            )

        slot = min(range(interval), key=score)
        phases[source_id] = slot
        counts[slot] += 1
        upstream_counts[slot][upstream] += 1
        used.setdefault(upstream, []).append(slot)
    return phases


def next_phase(now: datetime, phase: int, *, interval: int) -> datetime:
    """Next UTC minute strictly after ``now`` whose offset in the cycle is ``phase``."""

    current = now.astimezone(timezone.utc)
    elapsed = int((current - current.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() // 60)
    wait = (phase % interval - elapsed % interval) % interval
    candidate = current.replace(second=0, microsecond=0) + timedelta(minutes=wait)
    if candidate <= current:
        candidate += timedelta(minutes=interval)
    return candidate


def collects_feeds(task: Any) -> bool:
    return bool((getattr(task, "input_data", None) or {}).get("fetch", True))


def is_reader(task: Any) -> bool:
    return not bool((getattr(task, "input_data", None) or {}).get("skip_ai"))


def blocks_sample(task: Any, source_id: str) -> bool:
    """A feed collection covers this source. A read-only task does not."""

    if not collects_feeds(task):
        return False
    return covers(task, source_id)


def select_due_samples(
    due: list[tuple[str, int]],
    active_groups: list[list[str]],
    *,
    pool_size: int,
) -> list[str]:
    """Choose due sources that fit the pool and do not share an active upstream."""

    if any(not group for group in active_groups):
        return []
    room = pool_size - len(active_groups)
    busy_sources: set[str] = set()
    busy_upstreams: set[str] = set()
    for group in active_groups:
        busy_sources.update(group)
        busy_upstreams.update(source_upstream(source_id) for source_id in group if source_id in SOURCE_REGISTRY)
    chosen: list[str] = []
    for source_id, _phase in due:
        if room <= 0:
            break
        if source_id in busy_sources or source_upstream(source_id) in busy_upstreams:
            continue
        chosen.append(source_id)
        busy_sources.add(source_id)
        busy_upstreams.add(source_upstream(source_id))
        room -= 1
    return chosen


def _aware(value: datetime | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=now.tzinfo)
    return value.astimezone(now.tzinfo)


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
        self._states: dict[str, SituationScheduleState] = {}

    def _config(self):
        return get_config().situation

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self._config().timezone))

    def _phases(self) -> dict[str, int]:
        cfg = self._config()
        return assign_source_phases(list(SOURCE_REGISTRY), interval=cfg.interval_minutes)

    async def _load_states(self) -> None:
        persisted = await schedule_state_repository.load(self.KIND)
        for job_id, fields in persisted.items():
            state = self._states.setdefault(job_id, SituationScheduleState())
            for field, value in fields.items():
                setattr(state, field, value)

    async def _save(self, job_id: str) -> None:
        await schedule_state_repository.save(self.KIND, job_id, self._states[job_id])

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.schedule_enabled:
            logger.info("Situation scheduler disabled by configuration")
            return
        await self._load_states()
        now = self._now()
        for source_id, phase in self._phases().items():
            job_id = sample_job_id(source_id)
            state = self._states.setdefault(job_id, SituationScheduleState())
            scheduled = _aware(state.next_run_at, now)
            if scheduled is None or scheduled <= now:
                state.next_run_at = next_phase(now, phase, interval=cfg.interval_minutes)
            await self._save(job_id)
        read_state = self._states.setdefault(READ_JOB_ID, SituationScheduleState())
        read_at = _aware(read_state.next_run_at, now)
        if read_at is None or read_at <= now:
            read_state.next_run_at = now + timedelta(minutes=cfg.catch_up_interval_minutes)
        await self._save(READ_JOB_ID)
        await schedule_state_repository.remove(self.KIND, self.JOB_ID)
        self._states.pop(self.JOB_ID, None)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="globalid-situation-scheduler")
        logger.info(
            "Situation scheduler started: {} sources every {} minutes, pool {}",
            len(SOURCE_REGISTRY),
            cfg.interval_minutes,
            cfg.sample_pool_size,
        )

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

    async def _active_tasks(self) -> list[Task]:
        async with get_database() as db:
            return list(
                (
                    await db.execute(
                        select(Task)
                        .where(Task.task_type == TaskType.REFRESH_SITUATION_SOURCES, Task.status.in_(_ACTIVE))
                        .order_by(Task.created_at.desc())
                    )
                ).scalars()
            )

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
                await self._tick()
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("Situation scheduled refresh failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=cfg.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        cfg = self._config()
        now = self._now()
        phases = self._phases()
        active = await self._active_tasks()
        due: list[tuple[str, int]] = []
        for source_id, phase in sorted(phases.items(), key=lambda item: (item[1], item[0])):
            state = self._states.setdefault(sample_job_id(source_id), SituationScheduleState())
            scheduled = _aware(state.next_run_at, now)
            if scheduled is not None and now >= scheduled:
                due.append((source_id, phase))
        groups = [task_sources(task) for task in active if collects_feeds(task)]
        for source_id in select_due_samples(due, groups, pool_size=cfg.sample_pool_size):
            await self.trigger_job(sample_job_id(source_id), manual=False)
        await self._tick_read(now, active)

    async def _tick_read(self, now: datetime, active: list[Task]) -> None:
        cfg = self._config()
        state = self._states.setdefault(READ_JOB_ID, SituationScheduleState())
        scheduled = _aware(state.next_run_at, now)
        if scheduled is None or now < scheduled:
            return
        if any(is_reader(task) for task in active) or await self.backlog() <= 0:
            state.next_run_at = now + timedelta(minutes=cfg.catch_up_interval_minutes)
            await self._save(READ_JOB_ID)
            return
        await self.trigger_job(READ_JOB_ID, manual=False)

    async def trigger_job(self, job_id: str, *, manual: bool) -> dict[str, Any]:
        if job_id == self.JOB_ID:
            return await self._enqueue(
                job_id,
                manual=manual,
                task_name="Situation: collect reports and extract alerts",
                sources=None,
                fetch=True,
                skip_ai=False,
                rebuild=True,
                description="Manual Situation refresh; writes the database only",
            )
        if job_id == READ_JOB_ID:
            return await self._enqueue(
                job_id,
                manual=manual,
                task_name="Situation: read stored reports",
                sources=None,
                fetch=False,
                skip_ai=False,
                rebuild=True,
                description="Read situation reports that sampling already stored",
            )
        source_id = source_from_job(job_id)
        if source_id is None:
            raise ValueError(f"Situation job not found: {job_id}")
        return await self._enqueue(
            job_id,
            manual=manual,
            task_name=f"Situation: sample {source_label(source_id)}",
            sources=[source_id],
            fetch=True,
            skip_ai=True,
            rebuild=False,
            description="Scheduled situation sample; the task worker collects this feed",
        )

    async def _enqueue(
        self,
        job_id: str,
        *,
        manual: bool,
        task_name: str,
        sources: list[str] | None,
        fetch: bool,
        skip_ai: bool,
        rebuild: bool,
        description: str,
    ) -> dict[str, Any]:
        cfg = self._config()
        async with self._lock:
            active = await self._active_tasks()
            requested = list(sources or [])
            if job_id == READ_JOB_ID:
                conflict = next((task for task in active if is_reader(task)), None)
            elif sources is None:
                conflict = next((task for task in active if collects_feeds(task) or is_reader(task)), None)
            else:
                conflict = next((task for task in active if blocks_sample(task, requested[0])), None)
            state = self._states.setdefault(job_id, SituationScheduleState())
            if conflict is not None:
                phase = self._phases().get(requested[0]) if requested else None
                now = self._now()
                state.next_run_at = (
                    next_phase(now, phase, interval=cfg.interval_minutes)
                    if phase is not None
                    else now + timedelta(minutes=cfg.catch_up_interval_minutes)
                )
                state.last_status = "skipped"
                state.last_task_uuid = conflict.task_uuid
                await self._save(job_id)
                return {
                    "job_id": job_id,
                    "status": "skipped",
                    "task_uuid": conflict.task_uuid,
                    "reason": "A situation task already covers this work",
                }
            now = self._now()
            state.last_started_at = now
            try:
                task = await task_manager.create_task(
                    task_type=TaskType.REFRESH_SITUATION_SOURCES,
                    task_name=task_name,
                    priority=TaskPriority.NORMAL,
                    status=TaskStatus.QUEUED,
                    description=description,
                    input_data={
                        "situation_job_id": job_id,
                        "situation_role": "sample" if skip_ai else ("read" if not fetch else "refresh"),
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                        "fetch": fetch,
                        "export": False,
                        "skip_ai": skip_ai,
                        "rebuild": rebuild,
                        "sources": requested,
                        "document_limit": cfg.document_limit,
                        "ai_limit": cfg.ai_limit,
                    },
                    tags=["situation", "sample" if skip_ai else ("read" if not fetch else "refresh")],
                )
                state.last_task_uuid = task.task_uuid
                state.last_status = "queued"
                state.last_error = None
                return {"job_id": job_id, "status": "queued", "task_uuid": task.task_uuid}
            except Exception as exc:
                state.last_status = "failed"
                state.last_error = str(exc)
                raise
            finally:
                state.last_finished_at = self._now()
                phase = self._phases().get(requested[0]) if requested else None
                if phase is None:
                    state.next_run_at = state.last_finished_at + timedelta(minutes=cfg.catch_up_interval_minutes)
                else:
                    state.next_run_at = next_phase(state.last_finished_at, phase, interval=cfg.interval_minutes)
                await self._save(job_id)

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        await self._load_states()
        phases = self._phases()
        jobs: list[dict[str, Any]] = []
        for source_id, phase in sorted(phases.items(), key=lambda item: (item[1], item[0])):
            job_id = sample_job_id(source_id)
            state = self._states.get(job_id, SituationScheduleState())
            jobs.append(self._job_row(
                job_id=job_id,
                name=f"Situation sample: {source_label(source_id)}",
                state=state,
                interval=cfg.interval_minutes,
                phase_minute=phase,
                upstream=source_upstream(source_id),
                sources=[source_id],
            ))
        jobs.append(self._job_row(
            job_id=READ_JOB_ID,
            name="Situation: read stored reports",
            state=self._states.get(READ_JOB_ID, SituationScheduleState()),
            interval=cfg.catch_up_interval_minutes,
            phase_minute=None,
            upstream=None,
            sources=[],
        ))
        return {
            "timezone": cfg.timezone,
            "jobs": jobs,
        }

    def _job_row(
        self,
        *,
        job_id: str,
        name: str,
        state: SituationScheduleState,
        interval: int,
        phase_minute: int | None,
        upstream: str | None,
        sources: list[str],
    ) -> dict[str, Any]:
        cfg = self._config()
        return {
            "job_id": job_id,
            "name": name,
            "enabled": cfg.schedule_enabled,
            "timezone": cfg.timezone,
            "interval_minutes": interval,
            "daily_time": None,
            "next_run_at": state.next_run_at,
            "last_started_at": state.last_started_at,
            "last_finished_at": state.last_finished_at,
            "last_status": state.last_status,
            "last_error": state.last_error,
            "last_task_uuid": state.last_task_uuid,
            "phase_minute": phase_minute,
            "upstream": upstream,
            "sources": sources,
            "sample_pool_size": cfg.sample_pool_size,
            "catch_up_interval_minutes": cfg.catch_up_interval_minutes,
            "document_limit": cfg.document_limit,
            "ai_limit": cfg.ai_limit,
        }


situation_schedule_service = SituationScheduleService()

__all__ = [
    "SituationScheduleService",
    "assign_source_phases",
    "next_phase",
    "sample_job_id",
    "select_due_samples",
    "situation_schedule_service",
    "source_upstream",
]
