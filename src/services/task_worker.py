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

from sqlalchemy import and_, case, or_, select

from src.core import get_database, get_logger
from src.core.config import get_config
from src.domain import Task, TaskPriority, TaskStatus, TaskType
from src.services.task_executor import execute_task, recover_interrupted_tasks_on_startup
from src.services._lifecycle import safe_exception_summary
from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.task_manager import task_manager
from src.core.release_admission import COLLECTION_STORE_TASK_TYPES, release_admission
from src.ai.work_queue import AIWorkDispatcher, release_leases_on_startup, waiting_parent_uuids

logger = get_logger(__name__)

TASK_WORKER_CONFIG = get_config().task_worker
POLL_INTERVAL_SECONDS = TASK_WORKER_CONFIG.poll_interval_seconds
IDLE_LOG_EVERY = TASK_WORKER_CONFIG.idle_log_every
MAX_CONCURRENT_TASKS = TASK_WORKER_CONFIG.concurrency
GENERAL_WORKER_CONCURRENCY = TASK_WORKER_CONFIG.general_concurrency
RELEASE_WORKER_CONCURRENCY = TASK_WORKER_CONFIG.release_concurrency
MAX_CONCURRENT_KNOWLEDGE_SOURCE_TASKS = min(
    TASK_WORKER_CONFIG.knowledge_source_concurrency,
    GENERAL_WORKER_CONCURRENCY,
)
MIN_CONCURRENT_AI_TASKS = min(
    max(1, TASK_WORKER_CONFIG.ai_concurrency_min),
    MAX_CONCURRENT_TASKS,
)
TASK_HEARTBEAT_SECONDS = TASK_WORKER_CONFIG.task_heartbeat_seconds
SHUTDOWN_GRACE_SECONDS = TASK_WORKER_CONFIG.shutdown_grace_seconds
STALE_TASK_SECONDS = TASK_WORKER_CONFIG.stale_task_seconds
RECOVERY_SCAN_SECONDS = TASK_WORKER_CONFIG.recovery_scan_seconds
RUNTIME_LEASE_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_lease_ttl_seconds
RUNTIME_HEARTBEAT_TTL_SECONDS = TASK_WORKER_CONFIG.runtime_heartbeat_ttl_seconds
MODEL_ROUTE_RECOVERY_SCAN_SECONDS = max(
    60,
    min(300, TASK_WORKER_CONFIG.ai_concurrency_adjust_seconds * 3),
)

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
# Archival has a separate single-job lane; it cannot consume the main release
# slot merely because it shares the persisted EXPORT_DATA enum.
_claimed_raw_archive_uuids: set[str] = set()
_claimed_data_release_uuids: set[str] = set()
# Releases share one filesystem lane; their database readers use an MVCC
# snapshot, so ordinary collection/network/model work keeps its own lanes.
# These operations persist their checkpoints and are designed to be resumed.
# On a controlled worker restart, returning them to QUEUED is preferable to
# waiting for a long model timeout or treating the deployment as a failure.
GRACEFUL_REQUEUE_TASK_TYPES = (
    AI_TASK_TYPES | KNOWLEDGE_SOURCE_TASK_TYPES | {TaskType.SYNC_LITERATURE, TaskType.REFRESH_SITUATION_SOURCES}
)

ModelRouteLoader = Callable[[], Awaitable[list[dict[str, Any]]]]


def _release_memory_blocked_task_types(
    active_task_types: Iterable[TaskType],
    *,
    release_waiting: bool = False,
) -> set[TaskType]:
    """Serialize releases sharing output paths, without blocking collection.

    Export readers import one PostgreSQL snapshot. Latest formal facts and
    their dirty markers may advance independently throughout the release.
    """
    active = set(active_task_types)
    blocked: set[TaskType] = set()
    if active & RELEASE_EXCLUSIVE_TASK_TYPES:
        blocked.update(RELEASE_EXCLUSIVE_TASK_TYPES)
    return blocked


GENERAL_TASK_TYPES = frozenset(
    task_type
    for task_type in TaskType
    if task_type not in AI_TASK_TYPES and task_type not in RELEASE_EXCLUSIVE_TASK_TYPES
)


def _lane_blocked_task_types(
    executing_types: Iterable[TaskType],
    *,
    ai_capacity: int,
    general_capacity: int | None = None,
    release_capacity: int | None = None,
    knowledge_source_capacity: int | None = None,
) -> set[TaskType] | None:
    """Task types the next claim should skip, or None when every lane is full.

    Release, ordinary work, and AI parents do not share one counter. The AI
    lane size is the sum of routable per-model limits. Zero means the model
    center has nothing to route, so new AI parents wait while the other lanes
    stay open. Waiting parents are omitted by the caller and do not consume a
    lane. Pooled model calls keep their own per-model permits in the dispatcher.
    """
    general_limit = GENERAL_WORKER_CONCURRENCY if general_capacity is None else general_capacity
    release_limit = RELEASE_WORKER_CONCURRENCY if release_capacity is None else release_capacity
    knowledge_limit = (
        MAX_CONCURRENT_KNOWLEDGE_SOURCE_TASKS
        if knowledge_source_capacity is None
        else knowledge_source_capacity
    )
    executing = list(executing_types)
    general_count = sum(1 for task_type in executing if task_type in GENERAL_TASK_TYPES)
    release_count = sum(1 for task_type in executing if task_type in RELEASE_EXCLUSIVE_TASK_TYPES)
    ai_count = sum(1 for task_type in executing if task_type in AI_TASK_TYPES)
    knowledge_count = sum(1 for task_type in executing if task_type in KNOWLEDGE_SOURCE_TASK_TYPES)
    ai_limit = max(0, ai_capacity)

    general_full = general_count >= general_limit
    release_full = release_count >= release_limit
    ai_full = ai_count >= ai_limit
    if general_full and release_full and ai_full:
        return None

    blocked: set[TaskType] = set()
    if general_full:
        blocked.update(GENERAL_TASK_TYPES)
    elif knowledge_count >= knowledge_limit:
        blocked.update(KNOWLEDGE_SOURCE_TASK_TYPES)
    if release_full:
        blocked.update(RELEASE_EXCLUSIVE_TASK_TYPES)
    if ai_full:
        blocked.update(AI_TASK_TYPES)
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
        """Derive AI task slots from independent routable model limits."""

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

        self._route_ceiling = sum(
            max(1, int(route.get("runtime_model_capacity") or self.slots_per_route))
            for route in active_routes
        )
        previous = self._capacity
        self._capacity = self._route_ceiling
        self._last_route_refresh = now
        if previous != self._capacity:
            logger.info(
                "AI task slots adjusted from {} to {} from {} routable model limits",
                previous,
                self._capacity,
                len(active_routes),
            )
        return self._capacity

    def record_result(self, *, success: bool, error: Exception | None = None) -> int:
        """Route health and per-model limits own pressure handling."""
        return self._capacity


async def _load_runtime_routes() -> list[dict[str, Any]]:
    # Import lazily so worker startup remains independent from model-center modules
    # until adaptive scheduling is actually initialized.
    from src.ai.model_center import get_runtime_routes

    return await get_runtime_routes()


def _runtime_provider_capacity_snapshots() -> dict[str, dict[str, Any]]:
    from src.ai.model_center import runtime_provider_admission_snapshots

    return runtime_provider_admission_snapshots()


def _runtime_model_capacity_snapshots() -> dict[str, dict[str, Any]]:
    from src.ai.model_center import runtime_model_admission_snapshots

    return runtime_model_admission_snapshots()


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
        reports_only = Task.input_data["reports_only"].as_boolean().is_(True)
        raw_archive_only = Task.input_data["raw_archive_only"].as_boolean().is_(True)
        data_only = Task.input_data["data_only"].as_boolean().is_(True)
        static_data_compatible = or_(
            Task.input_data["content_only"].as_boolean().is_(True), reports_only,
            Task.input_data["code_only"].as_boolean().is_(True),
        )
        legacy_site = and_(Task.task_type == TaskType.EXPORT_DATA,
                           ~raw_archive_only, ~data_only, ~static_data_compatible)
        situation_data = and_(data_only, (Task.input_data["data_line"].as_string() == "situation").is_(True))
        surveillance_data = and_(data_only, Task.input_data["data_line"].as_string().is_distinct_from("situation"))
        situation_legacy_site = and_(Task.task_type == TaskType.EXPORT_DATA,
            ~raw_archive_only, ~data_only, ~reports_only,
            Task.input_data["code_only"].as_boolean().is_not(True),
            Task.input_data["situation_detached"].as_boolean().is_not(True))
        nonexclusive_export = or_(reports_only, raw_archive_only, data_only)
        active_tasks = (
            await db.execute(
                select(Task.task_uuid, Task.task_type, nonexclusive_export.label("nonexclusive"),
                       raw_archive_only.label("raw_archive_only"), data_only.label("data_only"),
                       legacy_site.label("legacy_site"), situation_legacy_site.label("situation_legacy"),
                       Task.input_data["data_line"].as_string().label("data_line")).where(
                    or_(
                        Task.status == TaskStatus.RUNNING,
                        and_(Task.status == TaskStatus.RETRYING, ~raw_archive_only),
                    )
                )
            )
        ).all()
        active_task_types = [
            task_type for task_uuid, task_type, is_nonexclusive, _is_archive, _is_data, _is_legacy, _is_situation_legacy, _data_line in active_tasks
            if not (task_type == TaskType.EXPORT_DATA and is_nonexclusive)
            and not (
                task_type in COLLECTION_STORE_TASK_TYPES
                and task_uuid in release_admission.cooperative_tasks
            )
        ]
        memory_blocked_task_types = _release_memory_blocked_task_types(
            active_task_types,
        )
        lane_blocked = set(blocked_task_types or set())
        blocked_task_types = lane_blocked | memory_blocked_task_types

        priority_rank = case(
            (Task.priority == TaskPriority.URGENT, 0),
            (Task.priority == TaskPriority.HIGH, 1),
            (Task.priority == TaskPriority.NORMAL, 2),
            else_=3,
        )
        query = (
            select(Task)
            .where(Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]))
            .order_by(
                case(
                    (and_(Task.task_type == TaskType.EXPORT_DATA, ~raw_archive_only), 0),
                    (and_(Task.task_type == TaskType.EXPORT_DATA, raw_archive_only), 2),
                    else_=1,
                ),
                priority_rank.asc(), Task.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            # Inspect a small window so a deferred task for one disease does
            # not prevent unrelated diseases from using available capacity.
            .limit(64)
        )
        if blocked_task_types:
            allowed = Task.task_type.notin_(list(blocked_task_types))
            # Archival and JSON own independent slots and artifact roots.
            allowed = or_(allowed, and_(Task.task_type == TaskType.EXPORT_DATA, or_(raw_archive_only, data_only)))
            if TaskType.EXPORT_DATA in memory_blocked_task_types and TaskType.EXPORT_DATA not in lane_blocked:
                # Reports read report snapshots only. They do not walk the
                # surveillance, literature, or Situation tables being stored.
                allowed = or_(allowed, and_(Task.task_type == TaskType.EXPORT_DATA, reports_only))
            query = query.where(allowed)
        if any(task_type == TaskType.EXPORT_DATA and is_archive
               for _uuid, task_type, _nonexclusive, is_archive, _is_data, _is_legacy, _is_situation_legacy, _data_line in active_tasks):
            query = query.where(~raw_archive_only)
        active_data = [(line or "surveillance") for _uuid, task_type, _nonexclusive,
            _is_archive, is_data, _is_legacy, _is_situation_legacy, line in active_tasks
            if task_type == TaskType.EXPORT_DATA and is_data]
        if active_data:
            query = query.where(~data_only)
            if "surveillance" in active_data:
                query = query.where(~legacy_site)
            if "situation" in active_data:
                query = query.where(~situation_legacy_site)
        if any(task_type == TaskType.EXPORT_DATA and is_legacy
               for _uuid, task_type, _nonexclusive, _is_archive, _is_data, is_legacy, _is_situation_legacy, _line in active_tasks):
            query = query.where(~surveillance_data)
        if any(task_type == TaskType.EXPORT_DATA and is_situation_legacy
               for _uuid, task_type, _nonexclusive, _is_archive, _is_data, _is_legacy, is_situation_legacy, _line in active_tasks):
            query = query.where(~situation_data)
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
        task.input_data = {**(task.input_data or {}), "ai_waiting": False}
        task.last_error = None
        await db.commit()
        if (task.input_data or {}).get("raw_archive_only") is True:
            _claimed_raw_archive_uuids.add(task.task_uuid)
        if (task.input_data or {}).get("data_only") is True:
            _claimed_data_release_uuids.add(task.task_uuid)
        return task.task_uuid, task.task_type


async def _claim_next_task_safely(worker_instance_id, *, blocked_task_types=None, excluded_task_uuids=None):
    try:
        return await _claim_next_task_uuid(
            worker_instance_id, blocked_task_types=blocked_task_types,
            excluded_task_uuids=excluded_task_uuids,
        )
    except Exception as exc:
        # A transient database outage must not unwind asyncio.run() while
        # active tasks hold transactions and the runtime guard owns the lease.
        logger.warning("Task claim failed; worker will retry: {}", exc)
        return None


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


async def _interrupt_resumable_tasks_for_restart(
    active_tasks: set[asyncio.Task[None]],
    task_uuids: dict[asyncio.Task[None], str],
    task_types: dict[asyncio.Task[None], TaskType],
    worker_instance_id: str,
) -> set[asyncio.Task[None]]:
    """Cancel only tasks whose owned lease can safely return to the queue."""
    interrupted = [
        (task, task_uuids.get(task), task_types.get(task))
        for task in active_tasks
        if task_uuids.get(task) and task_types.get(task) in GRACEFUL_REQUEUE_TASK_TYPES
    ]
    for task, _task_uuid, _task_type in interrupted:
        task.cancel()
    await asyncio.gather(*(task for task, _task_uuid, _task_type in interrupted), return_exceptions=True)
    requeued = await _requeue_interrupted_tasks_for_restart(
        [(task_uuid, task_type) for _task, task_uuid, task_type in interrupted],
        worker_instance_id,
    )
    logger.info("Requeued {} interrupted resumable task(s) after controlled restart", requeued)
    return {task for task, _task_uuid, _task_type in interrupted}


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
        "Task worker started (poll_interval={}s, general_workers={}, release_workers={}, ai_slots=per_model)",
        POLL_INTERVAL_SECONDS,
        GENERAL_WORKER_CONCURRENCY,
        RELEASE_WORKER_CONCURRENCY,
    )

    ai_concurrency = AdaptiveAIConcurrencyController(
        minimum=MIN_CONCURRENT_AI_TASKS,
        maximum=MAX_CONCURRENT_TASKS,
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

    released = await release_leases_on_startup()
    if released:
        logger.warning("Released {} model work lease(s) left by the previous worker", released)

    await ai_concurrency.refresh(force=True)

    stop_event = asyncio.Event()
    ai_stop_event = asyncio.Event()
    model_dispatcher = AIWorkDispatcher()
    model_dispatch_loop = asyncio.create_task(model_dispatcher.run(ai_stop_event), name="worker-model-dispatch")
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
        lease_loss_shutdown_timeout_seconds=SHUTDOWN_GRACE_SECONDS + 15,
        metadata=lambda: {
            "concurrency": MAX_CONCURRENT_TASKS,
            "general_concurrency": GENERAL_WORKER_CONCURRENCY,
            "release_concurrency": RELEASE_WORKER_CONCURRENCY,
            "ai_concurrency_max": ai_concurrency.capacity,
            "ai_concurrency_current": ai_concurrency.capacity,
            "ai_concurrency_adaptive": True,
            "ai_provider_capacities": _runtime_provider_capacity_snapshots(),
            "ai_model_capacities": _runtime_model_capacity_snapshots(),
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
            _claimed_raw_archive_uuids.discard(task_uuid)
            _claimed_data_release_uuids.discard(task_uuid)
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
    shutdown_grace_handled = False
    while not stop_event.is_set() or active_tasks:
        if not stop_event.is_set():
            await ai_concurrency.refresh()
            model_center_is_routable = ai_concurrency.capacity > 0
            if model_center_is_routable and model_center_was_routable is not True:
                await _resume_knowledge_repairs_after_model_recovery()
            model_center_was_routable = model_center_is_routable
        elif shutdown_deadline is None and not shutdown_grace_handled:
            shutdown_deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
            logger.info(
                "Worker is draining {} active task(s) for up to {}s before restart",
                len(active_tasks),
                SHUTDOWN_GRACE_SECONDS,
            )
        while not stop_event.is_set():
            waiting = waiting_parent_uuids()
            executing_types = [task_type for task, task_type in active_task_types.items()
                               if active_task_uuids.get(task) not in waiting
                               and active_task_uuids.get(task) not in _claimed_raw_archive_uuids
                               and active_task_uuids.get(task) not in _claimed_data_release_uuids]
            # Parent tasks use separate lanes. Model HTTP permits stay per model
            # inside AIWorkDispatcher and are not collapsed into one AI parent.
            lane_blocked = _lane_blocked_task_types(
                executing_types,
                ai_capacity=ai_concurrency.capacity,
            )
            if lane_blocked is None:
                # JSON and archival each own an independent single slot. They
                # can still be claimed when every ordinary/AI/static lane is full.
                lane_blocked = set(TaskType)
            claimed = await _claim_next_task_safely(
                instance_id,
                blocked_task_types=lane_blocked or None,
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
                interrupted = await _interrupt_resumable_tasks_for_restart(
                    active_tasks, active_task_uuids, active_task_types, instance_id,
                )
                active_tasks.difference_update(interrupted)
                for task in interrupted:
                    active_task_types.pop(task, None)
                    active_task_uuids.pop(task, None)
                logger.warning(
                    "Worker shutdown grace period elapsed; waiting for {} non-resumable task(s) to finish",
                    len(active_tasks),
                )
                # Non-resumable work keeps its lease and heartbeat until it
                # finishes (or the service manager's stop timeout expires).
                # Clear the deadline so waiting does not become a busy loop.
                shutdown_deadline = None
                shutdown_grace_handled = True
            continue

        idle_ticks += 1
        if idle_ticks % max(1, IDLE_LOG_EVERY) == 0:
            logger.info("Task worker idle, waiting for queued tasks")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    stop_event.set()
    ai_stop_event.set()
    await model_dispatch_loop
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
