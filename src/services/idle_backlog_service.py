"""Idle-time drain of the AI review backlogs.

Scheduled AI tasks and release exports always go first. While none is waiting
and few are running, this loop spends spare model capacity on the queues the
other automations leave behind: Research Radar articles and summaries,
knowledge sources, learning suggestions and Situation reports. Work is cut into
short slices, so foreground work is noticed again within minutes.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select

from src.control_plane.schedule_state import schedule_state_repository
from src.core import get_config, get_database, get_logger
from src.domain import Task, TaskStatus
from src.services.ai_content_governance_service import ReviewClaims, ai_content_governance_service as governance
from src.services.situation_notices.pipeline import (
    backlog_counts as situation_backlog_counts,
)

logger = get_logger(__name__)

SITUATION_DOCUMENTS_PER_SLICE = 40
SITUATION_REPORTS_PER_SLICE = 10
_ACTIVE_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)


@dataclass
class SliceResult:
    finalised: int = 0
    deferred: int = 0
    exhausted: bool = False
    outage: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


LaneWork = Callable[["Lane", int, int], Awaitable[SliceResult]]


@dataclass
class Lane:
    """One review line. ``claims`` and ``pass_finalised`` live for one pass over the queue."""

    name: str
    work: LaneWork
    claims: ReviewClaims | None = None
    next_at: float = 0.0
    failures: int = 0
    pass_finalised: int = 0


async def foreground_pressure(max_running_ai_tasks: int) -> str | None:
    """Why backlog work must wait, or ``None`` when the system is idle enough."""

    from src.services.task_worker import AI_TASK_TYPES, RELEASE_EXCLUSIVE_TASK_TYPES

    async with get_database() as db:
        rows = (
            await db.execute(
                select(Task.task_type, Task.status, func.count())
                .where(
                    Task.status.in_(_ACTIVE_TASK_STATUSES),
                    Task.task_type.in_(list(AI_TASK_TYPES | RELEASE_EXCLUSIVE_TASK_TYPES)),
                )
                .group_by(Task.task_type, Task.status)
            )
        ).all()
    waiting = running = 0
    for task_type, status, count in rows:
        if task_type in RELEASE_EXCLUSIVE_TASK_TYPES:
            return "release export is queued or running"
        if status == TaskStatus.RUNNING:
            running += count
        else:
            waiting += count
    if waiting:
        return f"{waiting} AI task(s) waiting for a worker"
    if running > max_running_ai_tasks:
        return f"{running} AI tasks running (limit {max_running_ai_tasks})"
    return None


async def _load_backlog_counts() -> dict[str, int]:
    async with get_database() as db:
        counts = await governance.backlog_counts(db)
    situation = await situation_backlog_counts()
    counts["situation"] = situation["documents"] + situation["extractions"]
    return counts


def _queued_lane(name: str, operation: str, options) -> Lane:
    async def work(lane: Lane, slice_size: int, workers: int) -> SliceResult:
        from src.services.model_operations import enqueue_model_operation
        from src.domain import TaskPriority
        task = await enqueue_model_operation(operation, options(slice_size, workers), priority=TaskPriority.LOW)
        return SliceResult(0, 1, True, None, {"queued_task_uuid": task.task_uuid})
    return Lane(name, work)


def stream_lane(name: str, kind: str) -> Lane:
    return _queued_lane(name, "governance_stream", lambda size, workers: {
        "kind": kind, "workers": workers, "fetch_size": min(64, size),
        "max_claims": size, "stall_limit": max(8, workers), "pause_seconds": 1.0})


def simple_lane(name: str, method: str) -> Lane:
    return _queued_lane(name, "governance_simple", lambda size, workers: {"method": method, "limit": min(size, 64)})


def situation_lane() -> Lane:
    return _queued_lane("situation", "idle_situation", lambda size, workers: {
        "document_limit": SITUATION_DOCUMENTS_PER_SLICE, "ai_limit": SITUATION_REPORTS_PER_SLICE})


def default_lanes() -> list[Lane]:
    # Situation first: its queue is small and user-visible. Articles precede summaries,
    # which wait on their parent article.
    return [
        situation_lane(),
        stream_lane("articles", "articles"),
        stream_lane("summaries", "summaries"),
        simple_lane("knowledge_sources", "review_knowledge_sources"),
        simple_lane("learning_suggestions", "review_learning_suggestions"),
    ]


class IdleBacklogService:
    JOB_KIND = "idle_backlog"
    JOB_ID = "ai-review-backlog"

    def __init__(
        self,
        lanes: list[Lane] | None = None,
        *,
        pressure: Callable[[int], Awaitable[str | None]] = foreground_pressure,
        counts: Callable[[], Awaitable[dict[str, int]]] = _load_backlog_counts,
        record: Callable[[dict[str, Any], datetime], Awaitable[None]] | None = None,
    ) -> None:
        self._lanes = lanes if lanes is not None else default_lanes()
        self._pressure = pressure
        self._counts = counts
        self._record = record or self._save_state
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_cycle: dict[str, Any] = {}

    def _config(self):
        return get_config().ai

    @property
    def enabled(self) -> bool:
        return bool(self._config().idle_backlog_enabled)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if not self.enabled:
            logger.info("Idle backlog processing is disabled")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="idle-backlog")
        logger.info("Idle backlog processing started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("Idle backlog processing did not stop within 10 seconds")
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._last_cycle = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not end the loop
                logger.exception("Idle backlog cycle failed: {}", exc)
                self._last_cycle = {"status": "error", "error": str(exc)[:500]}
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._config().idle_backlog_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self._task and not self._task.done()),
            "last_cycle": self._last_cycle,
        }

    async def process_once(self) -> dict[str, Any]:
        cfg = self._config()
        started = datetime.now(timezone.utc)
        cycle: dict[str, Any] = {"at": started.isoformat(), "status": "idle", "lanes": {}}
        due = [lane for lane in self._lanes if time.monotonic() >= lane.next_at]
        if not due:
            return await self._finish(cycle, started)
        reason = await self._pressure(cfg.idle_backlog_max_running_ai_tasks)
        if reason:
            cycle.update(status="busy", busy_reason=reason)
            return await self._finish(cycle, started)

        # A lane in the middle of a pass already knows it has work; only others need counting.
        counts = await self._counts() if any(lane.claims is None for lane in due) else {}
        cycle["backlog"] = counts
        for lane in due:
            if self._stop.is_set():
                break
            if lane.claims is None and not counts.get(lane.name):
                self._rest(lane, cfg.idle_backlog_rest_seconds)
                continue
            result = await self._run_slice(lane, cfg.idle_backlog_slice_size, cfg.idle_backlog_workers)
            self._schedule(lane, result, cfg.idle_backlog_interval_seconds, cfg.idle_backlog_rest_seconds)
            cycle["lanes"][lane.name] = {
                "finalised": result.finalised,
                "deferred": result.deferred,
                "exhausted": result.exhausted,
                "outage": result.outage,
                "next_in_seconds": round(max(0.0, lane.next_at - time.monotonic())),
            }
            if result.outage:
                break
            reason = await self._pressure(cfg.idle_backlog_max_running_ai_tasks)
            if reason:
                cycle.update(status="yielded", busy_reason=reason)
                break
        if cycle["lanes"] and cycle["status"] == "idle":
            cycle["status"] = "worked"
        return await self._finish(cycle, started)

    async def _run_slice(self, lane: Lane, slice_size: int, workers: int) -> SliceResult:
        try:
            return await lane.work(lane, slice_size, workers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - treated like a model outage: back off, retry later
            logger.warning("Idle backlog lane {} failed: {}", lane.name, exc)
            return SliceResult(outage=f"{type(exc).__name__}: {exc}"[:500])

    @staticmethod
    def _rest(lane: Lane, seconds: float) -> None:
        lane.claims = None
        lane.pass_finalised = 0
        lane.next_at = time.monotonic() + seconds

    def _schedule(self, lane: Lane, result: SliceResult, interval: float, rest: float) -> None:
        """Decide when the lane runs next.

        A pass keeps its claims across slices so rows that were deferred or are not
        reviewable yet are not fetched again until the pass ends. A pass that decided
        something is followed by another soon; one that decided nothing rests.
        """

        if result.outage:
            lane.failures += 1
            self._rest(lane, min(rest, interval * 2 ** min(lane.failures, 10)))
            return
        lane.failures = 0
        lane.pass_finalised += result.finalised
        if result.exhausted:
            self._rest(lane, interval if lane.pass_finalised else rest)

    async def _finish(self, cycle: dict[str, Any], started: datetime) -> dict[str, Any]:
        if cycle["lanes"] or cycle["status"] != self._last_cycle.get("status"):
            try:
                await self._record(cycle, started)
            except Exception as exc:  # noqa: BLE001 - state projection is best effort
                logger.warning("Unable to persist idle backlog state: {}", exc)
        self._last_cycle = cycle
        return cycle

    async def _save_state(self, cycle: dict[str, Any], started: datetime) -> None:
        await schedule_state_repository.save(
            self.JOB_KIND,
            self.JOB_ID,
            SimpleNamespace(
                next_run_at=None,
                last_started_at=started,
                last_finished_at=datetime.now(timezone.utc),
                last_status=cycle["status"],
                last_error=json.dumps(cycle, ensure_ascii=False, default=str),
                last_task_uuid=None,
            ),
        )


idle_backlog_service = IdleBacklogService()


__all__ = ["IdleBacklogService", "Lane", "SliceResult", "idle_backlog_service"]
