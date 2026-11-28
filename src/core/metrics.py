"""Lightweight, zero-dependency Prometheus text-format (v0.0.4) metrics registry."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

DEFAULT_HTTP_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in labels.items()]
    return "{" + ",".join(parts) + "}"


def _format_number(value: int | float) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _status_class(status_code: int) -> str:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return "unknown"
    if 100 <= code < 600:
        return f"{code // 100}xx"
    return "unknown"


def extract_route_template(request: Any) -> str:
    """Return a low-cardinality FastAPI/Starlette route template for a request.

    Never returns raw path strings containing resource IDs or UUIDs; falls back
    to ``"unmatched"`` whenever no route template is present in ``request.scope``.
    """
    scope = getattr(request, "scope", None)
    if isinstance(scope, Mapping):
        route = scope.get("route")
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str) and route_path.strip():
            return route_path.strip()
    return "unmatched"


def collect_db_pool_metrics() -> dict[str, int] | None:
    """Safely read SQLAlchemy pool statistics without initializing the engine."""
    try:
        from src.core import database as database_module
    except Exception:
        return None

    engine = getattr(database_module, "_engine", None)
    if engine is None:
        return None

    sync_engine = getattr(engine, "sync_engine", engine)
    pool = getattr(sync_engine, "pool", None)
    if pool is None:
        return None

    def _safe_int(attr_name: str) -> int | None:
        attr = getattr(pool, attr_name, None)
        if not callable(attr):
            return None
        try:
            value = attr()
        except Exception:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return None

    size = _safe_int("size")
    checked_in = _safe_int("checkedin")
    checked_out = _safe_int("checkedout")
    overflow = _safe_int("overflow")

    stats: dict[str, int] = {}
    if size is not None:
        stats["size"] = size
    if checked_in is not None:
        stats["checked_in"] = checked_in
    if checked_out is not None:
        stats["checked_out"] = checked_out
    if overflow is not None:
        stats["overflow"] = overflow
    return stats or None


@dataclass(frozen=True)
class OperationalMetricsSnapshot:
    """Point-in-time snapshot of live operational and business metrics."""

    task_queue_tasks: dict[tuple[str, str], int] | None = None
    publish_dirty_pending: dict[str, int] | None = None
    publish_dirty_oldest_age_seconds: float | None = None
    runtime_components_alive: dict[str, int] | None = None
    ai_models_enabled: int | None = None
    scrape_success: bool | None = None
    scrape_duration_seconds: float | None = None


def _clean_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return max(0, int(value))
    return None


def _clean_non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return max(0.0, float(value))
    return None


def _coerce_operational_snapshot(
    snapshot: OperationalMetricsSnapshot | Mapping[str, Any],
) -> OperationalMetricsSnapshot:
    if isinstance(snapshot, OperationalMetricsSnapshot):
        raw_task_queue = snapshot.task_queue_tasks
        raw_dirty_pending = snapshot.publish_dirty_pending
        raw_dirty_age = snapshot.publish_dirty_oldest_age_seconds
        raw_runtime = snapshot.runtime_components_alive
        raw_ai_models = snapshot.ai_models_enabled
        raw_scrape_success = snapshot.scrape_success
        raw_scrape_duration = snapshot.scrape_duration_seconds
    elif isinstance(snapshot, Mapping):
        raw_task_queue = snapshot.get("task_queue_tasks")
        raw_dirty_pending = snapshot.get("publish_dirty_pending")
        raw_dirty_age = snapshot.get("publish_dirty_oldest_age_seconds")
        raw_runtime = snapshot.get("runtime_components_alive")
        raw_ai_models = snapshot.get("ai_models_enabled")
        raw_scrape_success = snapshot.get("scrape_success")
        raw_scrape_duration = snapshot.get("scrape_duration_seconds")
    else:
        raise TypeError("Operational snapshot must be an OperationalMetricsSnapshot or Mapping")

    task_queue: dict[tuple[str, str], int] | None = None
    if isinstance(raw_task_queue, Mapping):
        task_queue = {}
        for key, value in raw_task_queue.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            status = str(key[0]).strip().lower()
            task_type = str(key[1]).strip().lower()
            cleaned = _clean_non_negative_int(value)
            if status and task_type and cleaned is not None:
                task_queue[(status, task_type)] = cleaned

    dirty_pending: dict[str, int] | None = None
    if isinstance(raw_dirty_pending, Mapping):
        dirty_pending = {}
        for scope_type, value in raw_dirty_pending.items():
            label = str(scope_type).strip().lower()
            cleaned = _clean_non_negative_int(value)
            if label and cleaned is not None:
                dirty_pending[label] = cleaned

    runtime_alive: dict[str, int] | None = None
    if isinstance(raw_runtime, Mapping):
        runtime_alive = {}
        for role, value in raw_runtime.items():
            label = str(role).strip().lower()
            cleaned = _clean_non_negative_int(value)
            if label and cleaned is not None:
                runtime_alive[label] = cleaned

    scrape_success: bool | None = None
    if raw_scrape_success is not None:
        scrape_success = bool(raw_scrape_success)

    return OperationalMetricsSnapshot(
        task_queue_tasks=task_queue,
        publish_dirty_pending=dirty_pending,
        publish_dirty_oldest_age_seconds=_clean_non_negative_float(raw_dirty_age),
        runtime_components_alive=runtime_alive,
        ai_models_enabled=_clean_non_negative_int(raw_ai_models),
        scrape_success=scrape_success,
        scrape_duration_seconds=_clean_non_negative_float(raw_scrape_duration),
    )


class PrometheusRegistry:
    """Thread-safe in-memory metrics registry rendering Prometheus text v0.0.4."""

    def __init__(self, buckets: tuple[float, ...] = DEFAULT_HTTP_BUCKETS) -> None:
        self._buckets = tuple(sorted(float(b) for b in buckets))
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._http_requests: dict[tuple[str, str, str], int] = {}
        self._http_duration_buckets: dict[tuple[str, str, str], list[int]] = {}
        self._http_duration_sum: dict[tuple[str, str, str], float] = {}
        self._http_duration_count: dict[tuple[str, str, str], int] = {}
        self._task_queue_snapshot: dict[str, float] = {}
        self._operational_snapshot: OperationalMetricsSnapshot | None = None

    def reset(self) -> None:
        """Reset all recorded samples (intended for unit tests)."""
        with self._lock:
            self._started_at = time.monotonic()
            self._http_requests.clear()
            self._http_duration_buckets.clear()
            self._http_duration_sum.clear()
            self._http_duration_count.clear()
            self._task_queue_snapshot.clear()
            self._operational_snapshot = None

    def record_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """Record an HTTP request counter and latency histogram observation."""
        norm_method = (method or "UNKNOWN").strip().upper() or "UNKNOWN"
        norm_route = (route or "unmatched").split("?", 1)[0].strip() or "unmatched"
        norm_status = _status_class(status_code)
        dur = max(0.0, float(duration_seconds)) if math.isfinite(duration_seconds) else 0.0
        key = (norm_method, norm_route, norm_status)

        with self._lock:
            self._http_requests[key] = self._http_requests.get(key, 0) + 1
            counts = self._http_duration_buckets.get(key)
            if counts is None:
                counts = [0] * (len(self._buckets) + 1)
                self._http_duration_buckets[key] = counts
            for idx, bound in enumerate(self._buckets):
                if dur <= bound:
                    counts[idx] += 1
            counts[-1] += 1  # +Inf bucket
            self._http_duration_sum[key] = self._http_duration_sum.get(key, 0.0) + dur
            self._http_duration_count[key] = self._http_duration_count.get(key, 0) + 1

    def set_task_queue_snapshot(self, counts: Mapping[str, int | float]) -> None:
        """Update the non-blocking in-memory snapshot of task queue counts by status."""
        cleaned: dict[str, float] = {}
        for status, value in counts.items():
            label = str(status).strip().lower()
            if not label:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                cleaned[label] = float(value)
        with self._lock:
            self._task_queue_snapshot = cleaned

    def clear_task_queue_snapshot(self) -> None:
        """Clear any previously stored task queue snapshot."""
        with self._lock:
            self._task_queue_snapshot.clear()

    def set_operational_snapshot(
        self,
        snapshot: OperationalMetricsSnapshot | Mapping[str, Any],
    ) -> None:
        """Store the latest cached operational and business metrics snapshot."""
        coerced = _coerce_operational_snapshot(snapshot)
        with self._lock:
            self._operational_snapshot = coerced

    def clear_operational_snapshot(self) -> None:
        """Clear any previously stored operational metrics snapshot."""
        with self._lock:
            self._operational_snapshot = None

    def render_prometheus(self, *, include_db_pool: bool = True) -> str:
        """Render all registered metrics in Prometheus text exposition format 0.0.4."""
        with self._lock:
            uptime = max(0.0, time.monotonic() - self._started_at)
            http_requests = dict(self._http_requests)
            http_buckets = {k: list(v) for k, v in self._http_duration_buckets.items()}
            http_sums = dict(self._http_duration_sum)
            http_counts = dict(self._http_duration_count)
            task_snapshot = dict(self._task_queue_snapshot)
            operational_snapshot = self._operational_snapshot

        lines: list[str] = []

        # Process uptime gauge
        lines.append("# HELP gids_process_uptime_seconds Process uptime in seconds.")
        lines.append("# TYPE gids_process_uptime_seconds gauge")
        lines.append(f"gids_process_uptime_seconds {_format_number(uptime)}")

        # HTTP request counter
        lines.append("# HELP gids_http_requests_total Total HTTP requests handled by the control-plane API.")
        lines.append("# TYPE gids_http_requests_total counter")
        for method, route, status_cls in sorted(http_requests):
            labels = _format_labels(
                {"method": method, "route": route, "status_class": status_cls}
            )
            lines.append(f"gids_http_requests_total{labels} {http_requests[(method, route, status_cls)]}")

        # HTTP request duration histogram
        lines.append(
            "# HELP gids_http_request_duration_seconds HTTP request latency in seconds."
        )
        lines.append("# TYPE gids_http_request_duration_seconds histogram")
        for key in sorted(http_buckets):
            method, route, status_cls = key
            counts = http_buckets[key]
            for idx, bound in enumerate(self._buckets):
                bucket_labels = _format_labels(
                    {
                        "method": method,
                        "route": route,
                        "status_class": status_cls,
                        "le": _format_number(bound),
                    }
                )
                lines.append(f"gids_http_request_duration_seconds_bucket{bucket_labels} {counts[idx]}")
            inf_labels = _format_labels(
                {
                    "method": method,
                    "route": route,
                    "status_class": status_cls,
                    "le": "+Inf",
                }
            )
            lines.append(f"gids_http_request_duration_seconds_bucket{inf_labels} {counts[-1]}")
            base_labels = _format_labels(
                {"method": method, "route": route, "status_class": status_cls}
            )
            lines.append(
                f"gids_http_request_duration_seconds_sum{base_labels} {_format_number(http_sums[key])}"
            )
            lines.append(
                f"gids_http_request_duration_seconds_count{base_labels} {http_counts[key]}"
            )

        # SQLAlchemy DB pool gauges (only emitted when engine is initialized)
        if include_db_pool:
            pool_stats = collect_db_pool_metrics()
            if pool_stats:
                gauge_specs = (
                    ("size", "gids_db_pool_size", "Configured SQLAlchemy connection pool size."),
                    ("checked_in", "gids_db_pool_checked_in", "Idle connections currently checked into the SQLAlchemy pool."),
                    ("checked_out", "gids_db_pool_checked_out", "Active connections currently checked out from the SQLAlchemy pool."),
                    ("overflow", "gids_db_pool_overflow", "Current SQLAlchemy connection pool overflow count."),
                )
                for key, metric_name, help_text in gauge_specs:
                    if key in pool_stats:
                        lines.append(f"# HELP {metric_name} {help_text}")
                        lines.append(f"# TYPE {metric_name} gauge")
                        lines.append(f"{metric_name} {pool_stats[key]}")

        # Task queue gauge (operational (status, task_type) snapshot takes precedence over legacy status-only snapshot)
        if operational_snapshot is not None and operational_snapshot.task_queue_tasks is not None:
            if operational_snapshot.task_queue_tasks:
                lines.append(
                    "# HELP gids_task_queue_tasks Count of active and recent failed tasks by status and task type."
                )
                lines.append("# TYPE gids_task_queue_tasks gauge")
                for status, task_type in sorted(operational_snapshot.task_queue_tasks):
                    labels = _format_labels({"status": status, "task_type": task_type})
                    lines.append(
                        f"gids_task_queue_tasks{labels} {operational_snapshot.task_queue_tasks[(status, task_type)]}"
                    )
        elif task_snapshot:
            lines.append("# HELP gids_task_queue_tasks Snapshot count of tasks by status.")
            lines.append("# TYPE gids_task_queue_tasks gauge")
            for status in sorted(task_snapshot):
                labels = _format_labels({"status": status})
                lines.append(f"gids_task_queue_tasks{labels} {_format_number(task_snapshot[status])}")

        # Operational and business gauges
        if operational_snapshot is not None:
            if operational_snapshot.publish_dirty_pending is not None:
                lines.append(
                    "# HELP gids_publish_dirty_pending Number of unpublished publish_dirty markers by scope type."
                )
                lines.append("# TYPE gids_publish_dirty_pending gauge")
                for scope_type in sorted(operational_snapshot.publish_dirty_pending):
                    labels = _format_labels({"scope_type": scope_type})
                    lines.append(
                        f"gids_publish_dirty_pending{labels} {operational_snapshot.publish_dirty_pending[scope_type]}"
                    )

            if operational_snapshot.publish_dirty_oldest_age_seconds is not None:
                lines.append(
                    "# HELP gids_publish_dirty_oldest_age_seconds Age in seconds of the oldest unpublished publish_dirty marker."
                )
                lines.append("# TYPE gids_publish_dirty_oldest_age_seconds gauge")
                lines.append(
                    "gids_publish_dirty_oldest_age_seconds "
                    f"{_format_number(operational_snapshot.publish_dirty_oldest_age_seconds)}"
                )

            if operational_snapshot.runtime_components_alive is not None:
                lines.append(
                    "# HELP gids_runtime_components_alive Number of live runtime component heartbeats by role."
                )
                lines.append("# TYPE gids_runtime_components_alive gauge")
                for role in sorted(operational_snapshot.runtime_components_alive):
                    labels = _format_labels({"role": role})
                    lines.append(
                        f"gids_runtime_components_alive{labels} {operational_snapshot.runtime_components_alive[role]}"
                    )

            if operational_snapshot.ai_models_enabled is not None:
                lines.append(
                    "# HELP gids_ai_models_enabled Number of enabled AI model routes backed by active providers."
                )
                lines.append("# TYPE gids_ai_models_enabled gauge")
                lines.append(f"gids_ai_models_enabled {operational_snapshot.ai_models_enabled}")

            if operational_snapshot.scrape_success is not None:
                lines.append(
                    "# HELP gids_operational_metrics_scrape_success Whether the last operational metrics scrape succeeded (1) or failed (0)."
                )
                lines.append("# TYPE gids_operational_metrics_scrape_success gauge")
                lines.append(
                    f"gids_operational_metrics_scrape_success {1 if operational_snapshot.scrape_success else 0}"
                )

            if operational_snapshot.scrape_duration_seconds is not None:
                lines.append(
                    "# HELP gids_operational_metrics_scrape_duration_seconds Duration in seconds of the last operational metrics scrape."
                )
                lines.append("# TYPE gids_operational_metrics_scrape_duration_seconds gauge")
                lines.append(
                    "gids_operational_metrics_scrape_duration_seconds "
                    f"{_format_number(operational_snapshot.scrape_duration_seconds)}"
                )

        return "\n".join(lines) + "\n"


metrics_registry = PrometheusRegistry()

__all__ = [
    "DEFAULT_HTTP_BUCKETS",
    "PROMETHEUS_CONTENT_TYPE",
    "OperationalMetricsSnapshot",
    "PrometheusRegistry",
    "collect_db_pool_metrics",
    "extract_route_template",
    "metrics_registry",
]
