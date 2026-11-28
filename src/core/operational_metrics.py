"""TTL-cached, bounded-timeout, fail-soft operational metrics collector."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
import os
import time
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.control_plane.runtime import runtime_registry
from src.core import database as database_module
from src.core.logging import get_logger
from src.core.metrics import (
    OperationalMetricsSnapshot,
    PrometheusRegistry,
    metrics_registry,
)
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.domain.collection import PublishDirty
from src.domain.task import Task, TaskStatus

logger = get_logger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 1.5
FAILED_LOOKBACK_HOURS = 24

ACTIVE_TASK_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
RuntimeProvider = Callable[[], Awaitable[tuple[list[dict[str, Any]], bool]]]
TaskQueueProvider = Callable[[], Awaitable[dict[tuple[str, str], int]]]
PublishDirtyProvider = Callable[[], Awaitable[tuple[dict[str, int], float]]]
AIModelsProvider = Callable[[], Awaitable[int]]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return default


def _env_float(name: str, default: float, *, minimum: float = 0.0, allow_zero: bool = True) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = float(raw.strip())
    except ValueError:
        return default
    if parsed < minimum or (not allow_zero and parsed <= 0.0):
        return default
    return parsed


def _utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _enum_label(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return str(raw).strip().lower()


async def _query_task_queue_counts(
    session: AsyncSession,
    now_utc: datetime,
) -> dict[tuple[str, str], int]:
    cutoff = now_utc - timedelta(hours=FAILED_LOOKBACK_HOURS)
    stmt = (
        select(Task.status, Task.task_type, func.count())
        .where(
            or_(
                Task.status.in_(ACTIVE_TASK_STATUSES),
                and_(
                    Task.status == TaskStatus.FAILED,
                    func.coalesce(Task.completed_at, Task.updated_at, Task.created_at) >= cutoff,
                ),
            )
        )
        .group_by(Task.status, Task.task_type)
    )
    rows = (await session.execute(stmt)).all()
    counts: dict[tuple[str, str], int] = {}
    for status, task_type, count in rows:
        status_label = _enum_label(status)
        type_label = _enum_label(task_type)
        if status_label and type_label:
            counts[(status_label, type_label)] = int(count or 0)
    return counts


async def _query_publish_dirty(
    session: AsyncSession,
    now_utc: datetime,
) -> tuple[dict[str, int], float]:
    stmt = (
        select(PublishDirty.line, func.count(), func.min(PublishDirty.marked_at))
        .where(PublishDirty.published_at.is_(None))
        .group_by(PublishDirty.line)
    )
    rows = (await session.execute(stmt)).all()
    pending_by_scope: dict[str, int] = {}
    oldest_marked_at: datetime | None = None
    for line, count, min_marked_at in rows:
        scope_label = str(line or "").strip().lower()
        if scope_label:
            pending_by_scope[scope_label] = int(count or 0)
        parsed_ts = _utc(min_marked_at)
        if parsed_ts is not None and (oldest_marked_at is None or parsed_ts < oldest_marked_at):
            oldest_marked_at = parsed_ts

    if oldest_marked_at is None:
        return pending_by_scope, 0.0
    oldest_age = max(0.0, (now_utc - oldest_marked_at).total_seconds())
    return pending_by_scope, oldest_age


async def _query_ai_models_enabled(session: AsyncSession) -> int:
    stmt = (
        select(func.count(AIModelConfig.id))
        .select_from(AIModelConfig)
        .join(AIProviderConfig, AIModelConfig.provider_id == AIProviderConfig.id)
        .where(
            AIModelConfig.is_enabled.is_(True),
            AIProviderConfig.is_active.is_(True),
        )
    )
    count = await session.scalar(stmt)
    return int(count or 0)


async def _safe_rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass


class OperationalMetricsCollector:
    """Collect live operational metrics with TTL caching, lock coalescing, and fail-soft guards."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: float | None = None,
        timeout_seconds: float | None = None,
        enabled: bool | None = None,
        session_factory: SessionFactory | None = None,
        runtime_provider: RuntimeProvider | None = None,
        task_queue_provider: TaskQueueProvider | None = None,
        publish_dirty_provider: PublishDirtyProvider | None = None,
        ai_models_provider: AIModelsProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._cache_ttl_override = cache_ttl_seconds
        self._timeout_override = timeout_seconds
        self._enabled_override = enabled
        self._session_factory = session_factory
        self._runtime_provider = runtime_provider
        self._task_queue_provider = task_queue_provider
        self._publish_dirty_provider = publish_dirty_provider
        self._ai_models_provider = ai_models_provider
        self._clock = clock
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        self._cached_snapshot: OperationalMetricsSnapshot | None = None
        self._cached_at: float | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return _env_bool("METRICS_OPERATIONAL_ENABLED", True)

    def cache_ttl_seconds(self) -> float:
        if self._cache_ttl_override is not None:
            return max(0.0, float(self._cache_ttl_override))
        return _env_float(
            "METRICS_OPERATIONAL_CACHE_TTL_SECONDS",
            DEFAULT_CACHE_TTL_SECONDS,
            minimum=0.0,
            allow_zero=True,
        )

    def timeout_seconds(self) -> float:
        if self._timeout_override is not None:
            return max(0.001, float(self._timeout_override))
        return _env_float(
            "METRICS_OPERATIONAL_TIMEOUT_SECONDS",
            DEFAULT_TIMEOUT_SECONDS,
            minimum=0.0,
            allow_zero=False,
        )

    def reset(self) -> None:
        """Clear cached snapshot state (intended for unit tests)."""
        self._cached_snapshot = None
        self._cached_at = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _is_cache_fresh(self, ttl: float) -> bool:
        if self._cached_snapshot is None or self._cached_at is None:
            return False
        if ttl <= 0.0:
            return False
        return (self._clock() - self._cached_at) < ttl

    def _resolve_session_factory(self) -> SessionFactory | None:
        if self._session_factory is not None:
            return self._session_factory
        engine = getattr(database_module, "_engine", None)
        if engine is None:
            return None
        existing_maker = getattr(database_module, "_session_maker", None)
        if existing_maker is not None:
            return existing_maker
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def _should_query_runtime(self) -> bool:
        if self._runtime_provider is not None:
            return True
        if getattr(database_module, "_engine", None) is not None:
            return True
        return getattr(runtime_registry, "_redis", None) is not None

    async def collect(self, *, force: bool = False) -> OperationalMetricsSnapshot | None:
        """Return a cached or freshly collected operational metrics snapshot."""
        if not self.is_enabled():
            return None

        ttl = self.cache_ttl_seconds()
        if not force and self._is_cache_fresh(ttl):
            return self._cached_snapshot

        async with self._get_lock():
            ttl = self.cache_ttl_seconds()
            if not force and self._is_cache_fresh(ttl):
                return self._cached_snapshot

            snapshot = await self._collect_uncached(timeout_seconds=self.timeout_seconds())
            self._cached_snapshot = snapshot
            self._cached_at = self._clock()
            return snapshot

    async def _collect_uncached(self, *, timeout_seconds: float) -> OperationalMetricsSnapshot:
        started = self._clock()
        now_utc = self._now_fn()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        partial: dict[str, Any] = {
            "task_queue_tasks": None,
            "publish_dirty_pending": None,
            "publish_dirty_oldest_age_seconds": None,
            "runtime_components_alive": None,
            "ai_models_enabled": None,
        }
        errors: list[str] = []

        async def _collect_db_metrics() -> None:
            need_task_db = self._task_queue_provider is None
            need_dirty_db = self._publish_dirty_provider is None
            need_ai_db = self._ai_models_provider is None

            session_factory = (
                self._resolve_session_factory()
                if (need_task_db or need_dirty_db or need_ai_db)
                else None
            )

            if self._task_queue_provider is not None:
                try:
                    partial["task_queue_tasks"] = dict(await self._task_queue_provider())
                except Exception as exc:
                    logger.debug("Operational metrics task queue provider failed: {}", exc)
                    errors.append("task_queue")

            if self._publish_dirty_provider is not None:
                try:
                    pending, oldest_age = await self._publish_dirty_provider()
                    partial["publish_dirty_pending"] = dict(pending)
                    partial["publish_dirty_oldest_age_seconds"] = max(0.0, float(oldest_age))
                except Exception as exc:
                    logger.debug("Operational metrics publish_dirty provider failed: {}", exc)
                    errors.append("publish_dirty")

            if self._ai_models_provider is not None:
                try:
                    partial["ai_models_enabled"] = int(await self._ai_models_provider())
                except Exception as exc:
                    logger.debug("Operational metrics AI models provider failed: {}", exc)
                    errors.append("ai_models")

            if session_factory is None:
                return

            try:
                async with session_factory() as session:
                    if need_task_db:
                        try:
                            partial["task_queue_tasks"] = await _query_task_queue_counts(session, now_utc)
                        except Exception as exc:
                            logger.debug("Operational metrics task queue query failed: {}", exc)
                            errors.append("task_queue")
                            await _safe_rollback(session)

                    if need_dirty_db:
                        try:
                            pending, oldest_age = await _query_publish_dirty(session, now_utc)
                            partial["publish_dirty_pending"] = pending
                            partial["publish_dirty_oldest_age_seconds"] = oldest_age
                        except Exception as exc:
                            logger.debug("Operational metrics publish_dirty query failed: {}", exc)
                            errors.append("publish_dirty")
                            await _safe_rollback(session)

                    if need_ai_db:
                        try:
                            partial["ai_models_enabled"] = await _query_ai_models_enabled(session)
                        except Exception as exc:
                            logger.debug("Operational metrics AI models query failed: {}", exc)
                            errors.append("ai_models")
                            await _safe_rollback(session)
            except Exception as exc:
                logger.debug("Operational metrics DB session acquisition failed: {}", exc)
                errors.append("db_session")

        async def _collect_runtime_metrics() -> None:
            if not self._should_query_runtime():
                return
            provider = self._runtime_provider or runtime_registry.list_services
            try:
                services, available = await provider()
                if not available:
                    logger.debug("Operational metrics runtime registry unavailable")
                    errors.append("runtime_components")
                    return
                counts: dict[str, int] = {}
                for item in services:
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("service", "")).strip().lower()
                    if role:
                        counts[role] = counts.get(role, 0) + 1
                partial["runtime_components_alive"] = counts
            except Exception as exc:
                logger.debug("Operational metrics runtime heartbeat collection failed: {}", exc)
                errors.append("runtime_components")

        async def _run_all() -> None:
            await asyncio.gather(_collect_db_metrics(), _collect_runtime_metrics())

        try:
            await asyncio.wait_for(_run_all(), timeout=timeout_seconds)
        except TimeoutError:
            logger.debug(
                "Operational metrics collection timed out after {:.3f}s",
                timeout_seconds,
            )
            errors.append("timeout")
        except Exception as exc:
            logger.debug("Operational metrics collection failed unexpectedly: {}", exc)
            errors.append("unexpected")

        duration = max(0.0, self._clock() - started)
        return OperationalMetricsSnapshot(
            task_queue_tasks=partial["task_queue_tasks"],
            publish_dirty_pending=partial["publish_dirty_pending"],
            publish_dirty_oldest_age_seconds=partial["publish_dirty_oldest_age_seconds"],
            runtime_components_alive=partial["runtime_components_alive"],
            ai_models_enabled=partial["ai_models_enabled"],
            scrape_success=(len(errors) == 0),
            scrape_duration_seconds=duration,
        )

    async def refresh(
        self,
        registry: PrometheusRegistry = metrics_registry,
        *,
        force: bool = False,
    ) -> OperationalMetricsSnapshot | None:
        """Collect and publish operational metrics into the given registry."""
        if not self.is_enabled():
            registry.clear_operational_snapshot()
            return None
        snapshot = await self.collect(force=force)
        if snapshot is not None:
            registry.set_operational_snapshot(snapshot)
        return snapshot


operational_metrics_collector = OperationalMetricsCollector()


async def refresh_operational_metrics(
    registry: PrometheusRegistry = metrics_registry,
    *,
    force: bool = False,
    collector: OperationalMetricsCollector | None = None,
) -> OperationalMetricsSnapshot | None:
    """Fail-soft helper wired into ``GET /metrics``."""
    target_collector = collector or operational_metrics_collector
    try:
        return await target_collector.refresh(registry, force=force)
    except Exception as exc:
        logger.debug("refresh_operational_metrics failed unexpectedly: {}", exc)
        registry.set_operational_snapshot(
            OperationalMetricsSnapshot(
                scrape_success=False,
                scrape_duration_seconds=0.0,
            )
        )
        return None


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "OperationalMetricsCollector",
    "operational_metrics_collector",
    "refresh_operational_metrics",
]
