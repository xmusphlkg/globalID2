"""Project catalogue records into execution routes without database or process state.

Configured capacities are limits, never observed occupancy. The runtime metrics
reader supplies the shared lease counts used by the API and worker heartbeats.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .health import (
    _combined_route_rate_limit_state,
    _combined_route_runtime_health_state,
    _effective_route_check_status,
    _parse_datetime,
    _route_routing_status,
    _utcnow,
)
from .options import route_available_at

if TYPE_CHECKING:
    from src.domain.ai_model_center import AIModelConfig

DEFAULT_PROVIDER_CAPACITY = 2
DEFAULT_MODEL_CAPACITY = 1
MODEL_CAPACITY_CEILING = 512
PROVIDER_CAPACITY_CEILING = 4096


def _positive_int(value: Any, default: int, *, maximum: int = 64) -> int:
    """Read a bounded positive runtime setting from a model-center payload."""
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _route_sort_key(route: dict[str, Any]) -> tuple[int, int, float, str]:
    """Keep user priority first, then prefer proven low-latency healthy routes."""
    try:
        priority = int(route.get("priority", 100))
    except (TypeError, ValueError):
        priority = 100
    try:
        streak = int(route.get("runtime_failure_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    try:
        latency = float(route.get("runtime_latency_ewma_ms"))
    except (TypeError, ValueError):
        latency = 0.0
    # Unknown latency remains neutral: configured order resolves the first call.
    return priority, streak, latency, str(route.get("model_key") or "")


@dataclass(frozen=True)
class RouteCapacity:
    model: int
    provider: int

    @classmethod
    def from_route(cls, route: Mapping[str, Any]) -> RouteCapacity:
        params = route.get("extra_params")
        params = params if isinstance(params, dict) else {}
        return cls(
            model=_positive_int(
                params.get("runtime_max_concurrency"),
                DEFAULT_MODEL_CAPACITY,
                maximum=MODEL_CAPACITY_CEILING,
            ),
            provider=_positive_int(
                route.get("runtime_provider_auto_max_concurrency"),
                DEFAULT_PROVIDER_CAPACITY,
                maximum=PROVIDER_CAPACITY_CEILING,
            ),
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "runtime_model_capacity": self.model,
            "runtime_provider_capacity": self.provider,
            "runtime_provider_max_capacity": self.provider,
            "runtime_model_inflight": 0,
            "runtime_provider_inflight": 0,
        }


def build_runtime_routes(
    models: Iterable[AIModelConfig], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Build one consistent-time snapshot from detached, provider-loaded rows."""
    now = now or _utcnow()
    routes: list[dict[str, Any]] = []
    for model in models:
        provider = model.provider
        if not provider:
            continue

        rate_limit_state = _combined_route_rate_limit_state(model, provider, now=now)
        runtime_health_state = _combined_route_runtime_health_state(
            model, provider, now=now
        )
        provider_status = str(provider.last_check_status or "").strip().lower()
        model_status = str(model.last_check_status or "").strip().lower()
        effective_status = _effective_route_check_status(model_status, provider_status)
        status_routable = effective_status != "unavailable"
        has_api_key = bool(provider.api_key)
        outside_window = not route_available_at(model.extra_params, now)
        if outside_window:
            routing_status = "outside_window"
            routing_status_reason = (
                "The route is outside its configured availability window."
            )
        else:
            routing_status, routing_status_reason = _route_routing_status(
                {"has_api_key": has_api_key},
                status_routable=status_routable,
                rate_limit_state=rate_limit_state,
                runtime_health_state=runtime_health_state,
            )

        route = {
            "model_id": model.id,
            "model_key": model.model_key,
            "model_name": model.model_name,
            "priority": model.priority,
            "provider_id": provider.id,
            "provider_key": provider.provider_key,
            "provider_name": provider.provider_name,
            "api_style": model.api_style or provider.api_style or "openai_compatible",
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "extra_headers": provider.extra_headers or {},
            "extra_config": provider.extra_config or {},
            "extra_params": model.extra_params or {},
            "temperature": model.temperature,
            "has_api_key": has_api_key,
            "within_availability": not outside_window,
            # Provider credential failures apply to every model under
            # the provider. Preserve that durable state even when a
            # sibling has an older model-level "available" result.
            "last_check_status": effective_status,
            "last_checked_at": (
                model.last_checked_at.isoformat() if model.last_checked_at else None
            ),
            "available_for_routing": routing_status == "routable",
            "routing_status": routing_status,
            "routing_status_reason": routing_status_reason,
            **rate_limit_state,
            **runtime_health_state,
        }
        routes.append(route)

    # Aggregate enabled model limits for provider telemetry. This number
    # does not gate requests; each model owns its own permits.
    provider_auto_maxima: dict[int, int] = {}
    for route in routes:
        provider_id = int(route["provider_id"])
        model_capacity = RouteCapacity.from_route(route).model
        provider_auto_maxima[provider_id] = (
            provider_auto_maxima.get(provider_id, 0) + model_capacity
        )

    for route in routes:
        route["runtime_provider_auto_max_concurrency"] = max(
            1,
            provider_auto_maxima.get(int(route["provider_id"]), 0),
        )
        route.update(RouteCapacity.from_route(route).snapshot())

    return sorted(routes, key=_route_sort_key)


def recovery_probe_candidates(
    routes: Iterable[dict[str, Any]],
    *,
    now: datetime,
    limit: int = 2,
    minimum_interval_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Select recovery checks using persisted timestamps, independent of a worker.

    The runtime leases the sweep once across workers. Each completed probe saves
    ``last_checked_at``; restarting a process never resets the probe interval.
    """
    due: list[dict[str, Any]] = []
    interval = max(30, int(minimum_interval_seconds))
    for route in routes:
        if not route.get("runtime_degraded") or not route.get("has_api_key"):
            continue
        if route.get("routing_status") == "outside_window":
            continue
        if route.get("runtime_failure_active") or route.get("rate_limit_active"):
            continue
        try:
            int(route["model_id"])
        except (KeyError, TypeError, ValueError):
            continue
        timestamps = [
            stamp
            for value in (
                route.get("last_checked_at"),
                route.get("runtime_last_failure_at"),
            )
            if (stamp := _parse_datetime(value)) is not None
        ]
        if timestamps and (now - max(timestamps)).total_seconds() < interval:
            continue
        due.append(route)
    due.sort(
        key=lambda route: (
            str(route.get("runtime_last_failure_at") or ""),
            _route_sort_key(route),
            int(route["model_id"]),
        )
    )
    return due[: max(0, int(limit))]
