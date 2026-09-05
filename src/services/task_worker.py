"""Standalone worker process for executing queued tasks.

This worker decouples task execution from the API process. The API only marks tasks
as queued, and this worker continuously polls and executes them.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
from datetime import datetime, timezone
import gc
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

from sqlalchemy import case, select

from src.core import get_database, get_logger
from src.core.config import get_config
from src.domain import Task, TaskPriority, TaskStatus, TaskType
from src.services.task_executor import execute_task, recover_interrupted_tasks_on_startup
from src.services._lifecycle import safe_exception_summary
from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.task_manager import task_manager

logger = get_logger(__name__)

TASK_WORKER_CONFIG = get_config().task_worker
POLL_INTERVAL_SECONDS = TASK_WORKER_CONFIG.poll_interval_seconds
IDLE_LOG_EVERY = TASK_WORKER_CONFIG.idle_log_every
MAX_CONCURRENT_TASKS = TASK_WORKER_CONFIG.concurrency
MAX_CONCURRENT_AI_TASKS = min(TASK_WORKER_CONFIG.ai_concurrency, MAX_CONCURRENT_TASKS)
MAX_CONCURRENT_KNOWLEDGE_SOURCE_TASKS = min(
    TASK_WORKER_CONFIG.knowledge_source_concurrency,
    MAX_CONCURRENT_TASKS,
)
MIN_CONCURRENT_AI_TASKS = min(
    max(1, TASK_WORKER_CONFIG.ai_concurrency_min),
    MAX_CONCURRENT_AI_TASKS,
)
TASK_HEARTBEAT_SECONDS = TASK_WORKER_CONFIG.task_heartbeat_seconds
SHUTDOWN_GRACE_SECONDS = TASK_WORKER_CONFIG.shutdown_grace_seconds
STALE_TASK_SECONDS = TASK_WORKER_CONFIG.stale_task_seconds
RECOVERY_SCAN_SECONDS = TASK_WORKER_CONFIG.recovery_scan_seconds
RUNTIME_LEASE_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_lease_ttl_seconds
RUNTIME_HEARTBEAT_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_heartbeat_ttl_seconds

_LIBC: ctypes.CDLL | None = None
_LIBC_LOOKUP_DONE = False

AI_TASK_TYPES = {
    TaskType.GENERATE_REPORT,
    TaskType.GENERATE_SECTION,
    TaskType.REVIEW_SECTION,
    TaskType.UPDATE_DISEASE_KNOWLEDGE,
    TaskType.AGENT_WORKFLOW,
    TaskType.ENRICH_LITERATURE,
    TaskType.DISCOVER_LITERATURE_GAPS,
}
KNOWLEDGE_SOURCE_TASK_TYPES = {TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES}
KNOWLEDGE_SERIAL_TASK_TYPES = {
    TaskType.UPDATE_DISEASE_KNOWLEDGE,
    TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
}
RELEASE_EXCLUSIVE_TASK_TYPES = {TaskType.EXPORT_DATA}
RELEASE_SHARED_BLOCKED_TASK_TYPES = (
    AI_TASK_TYPES
    | KNOWLEDGE_SOURCE_TASK_TYPES
    | {TaskType.SYNC_LITERATURE}
)
# These operations persist their checkpoints and are designed to be resumed.
# On a controlled worker restart, returning them to QUEUED is preferable to
# waiting for a long model timeout or treating the deployment as a failure.
GRACEFUL_REQUEUE_TASK_TYPES = AI_TASK_TYPES | KNOWLEDGE_SOURCE_TASK_TYPES

ModelRouteLoader = Callable[[], Awaitable[list[dict[str, Any]]]]


def _release_memory_blocked_task_types(active_task_types: Iterable[TaskType]) -> set[TaskType]:
    """Keep memory-heavy release export and literature/AI work from overlapping."""
    active = set(active_task_types)
    blocked: set[TaskType] = set()
    if active & RELEASE_EXCLUSIVE_TASK_TYPES:
        blocked.update(RELEASE_SHARED_BLOCKED_TASK_TYPES)
    if active & RELEASE_SHARED_BLOCKED_TASK_TYPES:
        blocked.update(RELEASE_EXCLUSIVE_TASK_TYPES)
    return blocked


@dataclass
class AdaptiveAIConcurrencyController:
    """AIMD gate for AI tasks, bounded by live model-center route capacity."""

    minimum: int
    maximum: int
    enabled: bool
    slots_per_route: int
    scale_up_successes: int
    adjust_seconds: int
    route_loader: ModelRouteLoader
    _capacity: int = field(init=False)
    _route_ceiling: int = field(init=False)
    _success_streak: int = field(default=0, init=False)
    _last_route_refresh: float = field(default=float("-inf"), init=False)
    _last_scale_up: float = field(default=float("-inf"), init=False)

    def __post_init__(self) -> None:
        self.minimum = max(1, min(self.minimum, self.maximum))
        self.maximum = max(self.minimum, self.maximum)
        self.scale_up_successes = max(1, self.scale_up_successes)
        self.slots_per_route = max(1, self.slots_per_route)
        self.adjust_seconds = max(1, self.adjust_seconds)
        self._capacity = self.minimum
        self._route_ceiling = self.maximum

    @property
    def capacity(self) -> int:
        return self._capacity

    async def refresh(self, *, force: bool = False) -> int:
        """Refresh the ceiling from routable models without disrupting running work."""
        if not self.enabled:
            self._capacity = self.maximum
            return self._capacity

        now = time.monotonic()
        if not force and now - self._last_route_refresh < self.adjust_seconds:
            return self._capacity

        try:
            routes = await self.route_loader()
        except Exception as exc:
            # Keep the last known safe budget through a transient DB/model-center failure.
            logger.warning("Adaptive AI concurrency route refresh failed: {}", exc)
            self._last_route_refresh = now
            return self._capacity

        active_routes = [route for route in routes if route.get("available_for_routing")]
        if not active_routes:
            # The Model Center is the authority on route admission. Keeping a
            # synthetic minimum slot when every route is cooling down turns
            # queued work into avoidable failures and retry churn.
            previous = self._capacity
            self._route_ceiling = 0
            self._capacity = 0
            self._last_route_refresh = now
            if previous != self._capacity:
                logger.warning(
                    "Adaptive AI concurrency paused from {} to 0; Model Center has no routable models",
                    previous,
                )
            return self._capacity

        provider_capacities: dict[Any, int] = {}
        for route in active_routes:
            provider_key = route.get("provider_id") or route.get("provider_key")
            if provider_key is None:
                continue
            try:
                # Runtime admission is the source of truth when it is present.
                # The route-slot setting remains a compatibility fallback for
                # callers that predate Model Center admission telemetry.
                capacity = max(
                    1,
                    int(route.get("runtime_provider_capacity", self.slots_per_route)),
                )
            except (TypeError, ValueError):
                capacity = self.slots_per_route
            provider_capacities[provider_key] = max(
                provider_capacities.get(provider_key, 0),
                capacity,
            )
        provider_count = len(provider_capacities)
        route_count = len(active_routes)
        self._route_ceiling = min(
            self.maximum,
            max(self.minimum, sum(provider_capacities.values())),
        )
        # One credential can back several models but still have one shared
        # personal-plan quota. Start at one task per provider; Model Center's
        # request admission gate enforces and earns further parallelism.
        baseline = min(self._route_ceiling, max(self.minimum, provider_count))
        previous = self._capacity
        if self._capacity > self._route_ceiling:
            self._capacity = self._route_ceiling
        elif self._capacity < baseline:
            self._capacity = baseline
        self._last_route_refresh = now
        if previous != self._capacity:
            logger.info(
                "Adaptive AI concurrency adjusted from {} to {} (routable_models={}, providers={}, provider_capacity={})",
                previous,
                self._capacity,
                len(active_routes),
                provider_count,
                sum(provider_capacities.values()),
            )
        return self._capacity

    def record_result(self, *, success: bool, error: Exception | None = None) -> int:
        """Increase slowly after success; immediately back off on model pressure."""
        if not self.enabled:
            return self._capacity
        if self._route_ceiling <= 0:
            # An in-flight task may finish after ``refresh`` has paused
            # admission. Its outcome must not reopen a slot without a fresh
            # Model Center route snapshot.
            return self._capacity
        if success:
            self._success_streak += 1
            now = time.monotonic()
            if (
                self._capacity < self._route_ceiling
                and self._success_streak >= self.scale_up_successes
                and now - self._last_scale_up >= self.adjust_seconds
            ):
                previous = self._capacity
                self._capacity += 1
                self._success_streak = 0
                self._last_scale_up = now
                logger.info("Adaptive AI concurrency increased from {} to {} after successful tasks", previous, self._capacity)
            return self._capacity

        self._success_streak = 0
        if error is None or not _is_model_pressure_error(error):
            return self._capacity
        previous = self._capacity
        self._capacity = max(self.minimum, self._capacity - max(1, self._capacity // 2))
        if previous != self._capacity:
            logger.warning("Adaptive AI concurrency reduced from {} to {} after model pressure: {}", previous, self._capacity, error)
        return self._capacity


def _is_model_pressure_error(error: Exception) -> bool:
    """Only model throttling/timeouts should shrink the shared AI budget."""
    try:
        from src.ai.model_center import is_rate_limit_error

        if is_rate_limit_error(error):
            return True
    except Exception:
        pass
    message = str(error).lower()
    return any(token in message for token in ("agent completion failed", "model request timeout", "rate limit", "quota exceeded"))


async def _load_runtime_routes() -> list[dict[str, Any]]:
    # Import lazily so worker startup remains independent from model-center modules
    # until adaptive scheduling is actually initialized.
    from src.ai.model_center import get_runtime_routes

    return await get_runtime_routes()


def _knowledge_task_disease_id(task: Task) -> str | None:
    """Return the single disease resource owned by a knowledge task, if any."""
    input_data = getattr(task, "input_data", None) or {}
    if not isinstance(input_data, dict):
        return None
    disease_id = str(input_data.get("disease_id") or input_data.get("disease") or "").strip()
    if disease_id:
        return disease_id.upper()
    disease_ids = input_data.get("disease_ids")
    if isinstance(disease_ids, list) and len(disease_ids) == 1:
        disease_id = str(disease_ids[0] or "").strip()
        return disease_id.upper() if disease_id else None
    return None


def _release_task_memory() -> None:
    """Return cyclic garbage and idle libc heap pages after a task finishes."""
    global _LIBC, _LIBC_LOOKUP_DONE

    gc.collect()
    if sys.platform != "linux":
        return

    if not _LIBC_LOOKUP_DONE:
        _LIBC_LOOKUP_DONE = True
        libc_name = ctypes.util.find_library("c")
        if libc_name:
            try:
                _LIBC = ctypes.CDLL(libc_name)
                _LIBC.malloc_trim.argtypes = [ctypes.c_size_t]
                _LIBC.malloc_trim.restype = ctypes.c_int
            except Exception as exc:
                _LIBC = None
                logger.debug("libc malloc_trim unavailable: {}", exc)

    if _LIBC is None:
        return

    try:
        _LIBC.malloc_trim(0)
    except Exception as exc:
        logger.debug("libc malloc_trim failed: {}", exc)


async def _claim_next_task_uuid(
    worker_instance_id: str,
    *,
    blocked_task_types: set[TaskType] | None = None,
    excluded_task_uuids: set[str] | None = None,
) -> Optional[tuple[str, TaskType]]:
    """Pick one runnable task and return its UUID.

    Claim must be atomic across worker processes. We lock one candidate row,
    mark it RUNNING in the same transaction, and then return the UUID.
    """
    async with get_database() as db:
        active_task_types = (
            await db.execute(
                select(Task.task_type).where(
                    Task.status.in_([TaskStatus.RUNNING, TaskStatus.RETRYING])
                )
            )
        ).scalars().all()
        memory_blocked_task_types = _release_memory_blocked_task_types(active_task_types)
        blocked_task_types = set(blocked_task_types or set()) | memory_blocked_task_types

        priority_rank = case(
            (Task.priority == TaskPriority.URGENT, 0),
            (Task.priority == TaskPriority.HIGH, 1),
            (Task.priority == TaskPriority.NORMAL, 2),
            else_=3,
        )
        query = (
            select(Task)
            .where(Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]))
            .order_by(priority_rank.asc(), Task.created_at.asc())
            .with_for_update(skip_locked=True)
            # Inspect a small window so a deferred task for one disease does
            # not prevent unrelated diseases from using available capacity.
            .limit(64)
        )
        if blocked_task_types:
            query = query.where(Task.task_type.notin_(list(blocked_task_types)))
        candidates = list((await db.execute(query)).scalars().all())
        if not candidates:
            return None

        active_knowledge_tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.task_type.in_(KNOWLEDGE_SERIAL_TASK_TYPES),
                        Task.status.in_([TaskStatus.RUNNING, TaskStatus.RETRYING]),
                    )
                )
            ).scalars().all()
        )
        active_disease_ids = {
            disease_id
            for active_task in active_knowledge_tasks
            if (disease_id := _knowledge_task_disease_id(active_task))
        }
        excluded_task_uuids = excluded_task_uuids or set()
        task = next(
            (
                candidate
                for candidate in candidates
                if candidate.task_uuid not in excluded_task_uuids
                and (
                    candidate.task_type not in KNOWLEDGE_SERIAL_TASK_TYPES
                or (disease_id := _knowledge_task_disease_id(candidate)) is None
                or disease_id not in active_disease_ids
                )
            ),
            None,
        )
        if task is None:
            return None

        now = datetime.now(timezone.utc)
        metadata = dict(task.metadata_ or {})
        metadata["task_lease"] = {
            "owner": worker_instance_id,
            "claimed_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
        }
        task.metadata_ = metadata
        task.status = TaskStatus.RUNNING
        task.last_error = None
        await db.commit()
        return task.task_uuid, task.task_type


async def _heartbeat_claimed_task(
    task_uuid: str,
    worker_instance_id: str,
    finished: asyncio.Event,
) -> None:
    """Keep the task lease fresh until execution reaches a terminal state."""
    while not finished.is_set():
        try:
            await asyncio.wait_for(finished.wait(), timeout=TASK_HEARTBEAT_SECONDS)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            owned = await task_manager.heartbeat_task_lease(
                task_uuid, worker_instance_id
            )
        except Exception as exc:
            # A short database outage must not terminate the task coroutine.
            # If it lasts past STALE_TASK_SECONDS, the worker singleton lease
            # still prevents a second worker from recovering this live task.
            logger.warning("Task heartbeat failed for {}: {}", task_uuid, exc)
            continue
        if not owned:
            # A normal terminal status releases the task lease before this
            # coroutine observes ``finished``.  Treat that race as an
            # informational stop; ownership safety is still enforced by the
            # conditional heartbeat update itself.
            logger.info(
                "Task lease heartbeat ended (terminal or ownership changed); task_uuid={} owner={}",
                task_uuid,
                worker_instance_id,
            )
            return


async def _requeue_interrupted_tasks_for_restart(
    interrupted_tasks: list[tuple[str, TaskType]],
    worker_instance_id: str,
) -> int:
    """Return safely resumable tasks to QUEUED after a controlled shutdown."""
    requeued = 0
    reason = "Released after controlled worker restart; queued for automatic continuation."
    for task_uuid, task_type in interrupted_tasks:
        if task_type not in GRACEFUL_REQUEUE_TASK_TYPES:
            continue
        if await task_manager.requeue_owned_task_lease(
            task_uuid,
            worker_instance_id,
            reason,
        ):
            requeued += 1
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="warning",
                title="Task Requeued After Worker Restart",
                content=reason,
                content_type="text",
            )
    return requeued


async def _recover_stale_tasks(
    stop_event: asyncio.Event, worker_instance_id: str
) -> None:
    """Continuously recover tasks orphaned after a hard worker termination."""
    while not stop_event.is_set():
        try:
            recovered = await recover_interrupted_tasks_on_startup(
                stale_after_seconds=STALE_TASK_SECONDS,
                exclude_owner=worker_instance_id,
            )
            if recovered:
                logger.warning("Recovered {} stale task(s) during worker sweep", recovered)
        except Exception as exc:
            logger.exception("Stale-task recovery sweep failed: {}", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECOVERY_SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _resume_knowledge_repairs_after_model_recovery() -> None:
    """Delegate one bounded terminal-repair wake-up to AI governance."""
    from src.services.ai_content_governance_service import ai_content_governance_service

    recovery_epoch = datetime.now(timezone.utc).isoformat()
    try:
        async with get_database() as db:
            result = (
                await ai_content_governance_service.requeue_knowledge_repairs_after_model_recovery(
                    db,
                    recovery_epoch=recovery_epoch,
                )
            )
        if result["requeued_count"]:
            logger.info(
                "Resumed {} terminal knowledge repair(s) after Model Center recovery epoch {}",
                result["requeued_count"],
                recovery_epoch,
            )
    except Exception as exc:
        logger.warning("Knowledge recovery wake-up skipped: {}", exc)


async def run_worker() -> None:
    """Main worker loop."""
    logger.info(
        "Task worker started (poll_interval={}s, concurrency={}, ai_concurrency={}..{}, adaptive_ai={})",
        POLL_INTERVAL_SECONDS,
        MAX_CONCURRENT_TASKS,
        MIN_CONCURRENT_AI_TASKS,
        MAX_CONCURRENT_AI_TASKS,
        TASK_WORKER_CONFIG.ai_dynamic_concurrency_enabled,
    )

    ai_concurrency = AdaptiveAIConcurrencyController(
        minimum=MIN_CONCURRENT_AI_TASKS,
        maximum=MAX_CONCURRENT_AI_TASKS,
        enabled=TASK_WORKER_CONFIG.ai_dynamic_concurrency_enabled,
        slots_per_route=TASK_WORKER_CONFIG.ai_concurrency_per_route,
        scale_up_successes=TASK_WORKER_CONFIG.ai_concurrency_scale_up_successes,
        adjust_seconds=TASK_WORKER_CONFIG.ai_concurrency_adjust_seconds,
        route_loader=_load_runtime_routes,
    )

    instance_id = runtime_registry.new_instance_id("worker")
    lease_wait_seconds = max(5, min(30, RUNTIME_LEASE_TTL_SECONDS // 3))
    while not await runtime_registry.acquire_lease(
        "worker", instance_id, ttl_seconds=RUNTIME_LEASE_TTL_SECONDS
    ):
        if await runtime_registry.release_dead_local_instance_lease("worker"):
            continue
        # A killed worker can leave a Redis lease behind until its TTL expires.
        # A same-host dead owner is removed above; other hosts and live owners
        # still wait so exclusivity remains fail-closed.
        logger.warning(
            "Worker singleton lease is held; waiting up to {}s before retrying acquisition",
            lease_wait_seconds,
        )
        await asyncio.sleep(lease_wait_seconds)
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)

    recovered = await recover_interrupted_tasks_on_startup(
        stale_after_seconds=STALE_TASK_SECONDS
    )
    if recovered:
        logger.warning("Recovered {} interrupted task(s) on worker startup", recovered)

    await ai_concurrency.refresh(force=True)

    stop_event = asyncio.Event()
    lease_lost = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _threaded_lease_lost() -> None:
        loop.call_soon_threadsafe(lease_lost.set)
        loop.call_soon_threadsafe(stop_event.set)

    runtime_guard = runtime_registry.start_threaded_guard(
        "worker",
        instance_id,
        lease_ttl_seconds=RUNTIME_LEASE_TTL_SECONDS,
        heartbeat_ttl_seconds=RUNTIME_HEARTBEAT_TTL_SECONDS,
        metadata=lambda: {
            "concurrency": MAX_CONCURRENT_TASKS,
            "ai_concurrency_max": MAX_CONCURRENT_AI_TASKS,
            "ai_concurrency_current": ai_concurrency.capacity,
            "ai_concurrency_adaptive": TASK_WORKER_CONFIG.ai_dynamic_concurrency_enabled,
        },
        on_lease_lost=_threaded_lease_lost,
    )
    recovery_sweep = asyncio.create_task(
        _recover_stale_tasks(stop_event, instance_id),
        name="control-plane-worker-task-recovery",
    )
    await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)

    def _request_stop() -> None:
        logger.info("Stop signal received, task worker shutting down soon")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Fallback for environments that do not support signal handlers.
            pass

    async def _run_claimed_task(task_uuid: str, task_type: TaskType) -> None:
        task_logger = logger.bind(task_uuid=task_uuid, worker_instance_id=instance_id)
        finished = asyncio.Event()
        task_heartbeat = asyncio.create_task(
            _heartbeat_claimed_task(task_uuid, instance_id, finished),
            name=f"task-heartbeat-{task_uuid}",
        )
        succeeded = False
        execution_error: Exception | None = None
        try:
            task_logger.info("Executing claimed task")
            await control_plane_events.publish(
                "task.claimed",
                resource_type="task",
                resource_id=task_uuid,
                data={"worker_instance_id": instance_id},
            )
            await execute_task(task_uuid)
            succeeded = True
        except Exception as exc:
            execution_error = exc
            task_logger.exception("Worker failed executing task: {}", exc)
            error_summary = safe_exception_summary(exc)
            try:
                if await task_manager.fail_owned_task_lease(
                    task_uuid, instance_id, error_summary
                ):
                    await task_manager.add_workbook_entry(
                        task_uuid,
                        entry_type="error",
                        title="Worker Execution Failed",
                        content=error_summary,
                        content_type="text",
                    )
            except Exception as finalize_exc:
                task_logger.exception(
                    "Failed to persist worker execution failure: {}", finalize_exc
                )
        finally:
            if task_type in AI_TASK_TYPES:
                ai_concurrency.record_result(success=succeeded, error=execution_error)
            finished.set()
            await task_heartbeat
            _release_task_memory()

    active_tasks: set[asyncio.Task[None]] = set()
    active_task_types: dict[asyncio.Task[None], TaskType] = {}
    active_task_uuids: dict[asyncio.Task[None], str] = {}
    model_center_was_routable: bool | None = None
    idle_ticks = 0
    shutdown_deadline: float | None = None
    while not stop_event.is_set() or active_tasks:
        if not stop_event.is_set():
            await ai_concurrency.refresh()
            model_center_is_routable = ai_concurrency.capacity > 0
            if model_center_is_routable and model_center_was_routable is not True:
                await _resume_knowledge_repairs_after_model_recovery()
            model_center_was_routable = model_center_is_routable
        elif shutdown_deadline is None:
            shutdown_deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
            logger.info(
                "Worker is draining {} active task(s) for up to {}s before restart",
                len(active_tasks),
                SHUTDOWN_GRACE_SECONDS,
            )
        while not stop_event.is_set() and len(active_tasks) < MAX_CONCURRENT_TASKS:
            active_ai_tasks = sum(
                1 for task_type in active_task_types.values() if task_type in AI_TASK_TYPES
            )
            active_source_tasks = sum(
                1
                for task_type in active_task_types.values()
                if task_type in KNOWLEDGE_SOURCE_TASK_TYPES
            )
            blocked_task_types: set[TaskType] = set()
            if active_ai_tasks >= ai_concurrency.capacity:
                blocked_task_types.update(AI_TASK_TYPES)
            if active_source_tasks >= MAX_CONCURRENT_KNOWLEDGE_SOURCE_TASKS:
                blocked_task_types.update(KNOWLEDGE_SOURCE_TASK_TYPES)
            claimed = await _claim_next_task_uuid(
                instance_id,
                blocked_task_types=blocked_task_types or None,
                excluded_task_uuids=set(active_task_uuids.values()),
            )
            if not claimed:
                break
            task_uuid, task_type = claimed

            idle_ticks = 0
            task = asyncio.create_task(_run_claimed_task(task_uuid, task_type), name=f"task-{task_uuid}")
            active_tasks.add(task)
            active_task_types[task] = task_type
            active_task_uuids[task] = task_uuid

        if active_tasks:
            timeout = POLL_INTERVAL_SECONDS
            if shutdown_deadline is not None:
                timeout = min(timeout, max(0.0, shutdown_deadline - time.monotonic()))
            done, pending = await asyncio.wait(
                active_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                active_task_types.pop(task, None)
                active_task_uuids.pop(task, None)
            active_tasks = set(pending)
            if done:
                idle_ticks = 0
            if shutdown_deadline is not None and time.monotonic() >= shutdown_deadline and active_tasks:
                interrupted = [
                    (task, active_task_uuids.get(task), active_task_types.get(task))
                    for task in active_tasks
                ]
                logger.warning(
                    "Worker shutdown grace period elapsed; interrupting {} resumable task(s)",
                    len(interrupted),
                )
                for task, _task_uuid, _task_type in interrupted:
                    task.cancel()
                await asyncio.gather(*(task for task, _task_uuid, _task_type in interrupted), return_exceptions=True)
                requeued = await _requeue_interrupted_tasks_for_restart(
                    [
                        (task_uuid, task_type)
                        for _task, task_uuid, task_type in interrupted
                        if task_uuid and task_type is not None
                    ],
                    instance_id,
                )
                logger.warning(
                    "Requeued {} interrupted resumable task(s) after controlled restart",
                    requeued,
                )
                active_tasks.clear()
                active_task_types.clear()
                active_task_uuids.clear()
            continue

        idle_ticks += 1
        if idle_ticks % max(1, IDLE_LOG_EVERY) == 0:
            logger.info("Task worker idle, waiting for queued tasks")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    stop_event.set()
    await recovery_sweep
    await asyncio.to_thread(runtime_guard.stop)
    await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
    task_manager.set_broadcast_hook(None)
    await runtime_registry.release_lease("worker", instance_id)
    await runtime_registry.remove_heartbeat("worker", instance_id)
    await runtime_registry.close()
    await control_plane_events.close()
    logger.info("Task worker stopped")
    if lease_lost.is_set():
        raise RuntimeError("Task worker stopped because its singleton lease was lost")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except RuntimeError as exc:
        if "singleton lease" in str(exc).lower():
            # A second owner is an expected deployment race, not a crash loop.
            # systemd treats 75 as a deliberate no-op and preserves the useful
            # diagnostic without repeatedly restarting into the active lease.
            logger.warning("Task worker did not start because the singleton lease is held: {}", exc)
            raise SystemExit(75) from exc
        raise


if __name__ == "__main__":
    main()
