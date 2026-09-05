"""AI model center service: schema bootstrap, routing, and health checks."""

from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core import get_config, get_db, get_engine, get_logger
from src.domain import AIModelConfig, AIProviderConfig

logger = get_logger(__name__)

_schema_ready_engine: Any = None
_bootstrap_checked_engine: Any = None
_bootstrap_checked_at: Optional[float] = None
_bootstrap_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakKeyDictionary()
_BOOTSTRAP_RECHECK_SECONDS = 60.0
_ROUTING_STATE_KEY = "routing_state"
_RUNTIME_FAILURE_KINDS = {"timeout", "connection", "upstream", "structured_output"}
_RUNTIME_FAILURE_COOLDOWN_CAP_SECONDS = 600
_PROVIDER_TIMEOUT_CIRCUIT_THRESHOLD = 2
_PROVIDER_FAILURE_RECENCY_WINDOW = timedelta(minutes=10)
_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD = 8
_DEFAULT_PROVIDER_ADMISSION_MAX_CONCURRENCY = 2
_DEFAULT_MODEL_ADMISSION_MAX_CONCURRENCY = 1
_DEFAULT_ADMISSION_SUCCESS_SCALE_UP = 2
_MODEL_TEST_MARKER = "globalid-model-test-ok"
_MODEL_TEST_PROMPT = (
    "This is a production model-center health check. "
    f"Reply with exactly this text and nothing else: {_MODEL_TEST_MARKER}"
)
_STRUCTURED_MODEL_TEST_PROMPT = (
    "This is a production model-center workload probe. Return JSON only, with "
    "exactly this shape: {\"status\":\"globalid-structured-probe-ok\","
    "\"items\":[{\"id\":1,\"summary\":\"ok\"}]}. Do not wrap it in markdown."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _extract_routing_state(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    state = payload.get(_ROUTING_STATE_KEY)
    return dict(state) if isinstance(state, dict) else {}


def _write_routing_state(payload: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(payload or {}) if isinstance(payload, dict) else {}
    if state:
        updated[_ROUTING_STATE_KEY] = state
    else:
        updated.pop(_ROUTING_STATE_KEY, None)
    return updated


def _rate_limit_state(payload: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utcnow()
    state = _extract_routing_state(payload)
    cooldown_until = _parse_datetime(state.get("cooldown_until"))
    last_rate_limit_at = _parse_datetime(state.get("last_rate_limit_at"))
    active = bool(cooldown_until and cooldown_until > now)
    remaining_seconds = max(0, int((cooldown_until - now).total_seconds())) if active and cooldown_until else 0
    try:
        count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        count = 0

    return {
        "cooldown_until": cooldown_until,
        "cooldown_until_iso": cooldown_until.isoformat() if cooldown_until else None,
        "last_rate_limit_at": last_rate_limit_at,
        "last_rate_limit_at_iso": last_rate_limit_at.isoformat() if last_rate_limit_at else None,
        "rate_limit_count": count,
        "rate_limit_active": active,
        "rate_limit_remaining_seconds": remaining_seconds,
    }


def _mark_payload_rate_limited(payload: Any, cooldown_until: datetime, occurred_at: datetime) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    try:
        current_count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        current_count = 0
    state["cooldown_until"] = cooldown_until.isoformat()
    state["last_rate_limit_at"] = occurred_at.isoformat()
    state["rate_limit_count"] = current_count + 1
    return _write_routing_state(payload, state)


def _clear_payload_rate_limit(payload: Any, recovered_at: Optional[datetime] = None) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    if not state:
        return dict(payload or {}) if isinstance(payload, dict) else {}

    state.pop("cooldown_until", None)
    if recovered_at is not None:
        state["last_recovered_at"] = recovered_at.isoformat()
    return _write_routing_state(payload, state)


def _runtime_health_state(payload: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read production-call health telemetry stored alongside routing settings.

    Health-check probes are deliberately small and cannot establish that a
    route is suitable for a long, structured workload.  Keep the latter's
    signal separate from quota state so routing can temporarily avoid a route
    without claiming that its credential is invalid.
    """
    now = now or _utcnow()
    state = _extract_routing_state(payload)
    cooldown_until = _parse_datetime(state.get("runtime_cooldown_until"))
    active = bool(cooldown_until and cooldown_until > now)

    def _integer(name: str) -> int:
        try:
            return max(0, int(state.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    def _number(name: str) -> Optional[float]:
        try:
            value = float(state.get(name))
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    return {
        "runtime_failure_active": active,
        "runtime_failure_cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "runtime_failure_remaining_seconds": (
            max(0, int((cooldown_until - now).total_seconds()))
            if active and cooldown_until
            else 0
        ),
        "runtime_failure_kind": str(state.get("last_runtime_failure_kind") or "").strip() or None,
        "runtime_failure_streak": _integer("runtime_failure_streak"),
        "runtime_failure_count": _integer("runtime_failure_count"),
        "runtime_timeout_count": _integer("runtime_timeout_count"),
        "runtime_success_count": _integer("runtime_success_count"),
        "runtime_latency_ewma_ms": _number("runtime_latency_ewma_ms"),
        "runtime_last_latency_ms": _number("runtime_last_latency_ms"),
        "runtime_last_failure_at": _parse_datetime(state.get("last_runtime_failure_at")),
        "runtime_last_success_at": _parse_datetime(state.get("last_runtime_success_at")),
        "runtime_last_error": str(state.get("last_runtime_error") or "").strip() or None,
    }


def _runtime_failure_kind(error: Any) -> Optional[str]:
    """Classify only transport failures that justify a routing circuit break."""
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    status_code = getattr(error, "status_code", None)
    try:
        if status_code is not None and int(status_code) >= 500:
            return "upstream"
    except (TypeError, ValueError):
        pass

    message = str(error or "").lower()
    if any(
        marker in message
        for marker in (
            "malformed structured response",
            "invalid structured json",
        )
    ):
        return "structured_output"
    if any(marker in message for marker in ("timeout", "timed out", "readtimeout", "connecttimeout")):
        return "timeout"
    if any(
        marker in message
        for marker in (
            "empty completion response",
            "connection error",
            "connect error",
            "connection reset",
            "connection refused",
            "network is unreachable",
            "remoteprotocolerror",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
        )
    ):
        return "connection"
    return None


def _runtime_failure_cooldown_seconds(
    *, base_seconds: int, failure_streak: int
) -> int:
    """Bounded exponential backoff for a route with repeated production failures."""
    base = max(5, int(base_seconds or 0))
    return min(
        _RUNTIME_FAILURE_COOLDOWN_CAP_SECONDS,
        base * (2 ** max(0, min(failure_streak - 1, 4))),
    )


def _write_runtime_failure(
    payload: Any,
    *,
    kind: str,
    error: str,
    occurred_at: datetime,
    duration_seconds: Optional[float],
    cooldown_seconds: int,
) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    try:
        previous_streak = max(0, int(state.get("runtime_failure_streak") or 0))
    except (TypeError, ValueError):
        previous_streak = 0
    try:
        previous_count = max(0, int(state.get("runtime_failure_count") or 0))
    except (TypeError, ValueError):
        previous_count = 0
    try:
        previous_timeouts = max(0, int(state.get("runtime_timeout_count") or 0))
    except (TypeError, ValueError):
        previous_timeouts = 0

    streak = previous_streak + 1
    state.update(
        {
            "runtime_failure_streak": streak,
            "runtime_failure_count": previous_count + 1,
            "last_runtime_failure_kind": kind,
            "last_runtime_failure_at": occurred_at.isoformat(),
            "last_runtime_error": str(error or "")[:1000],
            "runtime_cooldown_until": (
                occurred_at
                + timedelta(
                    seconds=_runtime_failure_cooldown_seconds(
                        base_seconds=cooldown_seconds,
                        failure_streak=streak,
                    )
                )
            ).isoformat(),
        }
    )
    if kind == "timeout":
        state["runtime_timeout_count"] = previous_timeouts + 1
    if duration_seconds is not None:
        state["runtime_last_latency_ms"] = round(max(0.0, duration_seconds) * 1000, 1)
    return _write_routing_state(payload, state)


def _write_runtime_success(
    payload: Any,
    *,
    occurred_at: datetime,
    duration_seconds: Optional[float],
) -> Dict[str, Any]:
    state = _extract_routing_state(payload)
    try:
        successes = max(0, int(state.get("runtime_success_count") or 0))
    except (TypeError, ValueError):
        successes = 0
    state["runtime_success_count"] = successes + 1
    state["runtime_failure_streak"] = 0
    state["last_runtime_success_at"] = occurred_at.isoformat()
    state.pop("runtime_cooldown_until", None)
    state.pop("last_runtime_failure_kind", None)
    state.pop("last_runtime_error", None)
    if duration_seconds is not None:
        latency_ms = round(max(0.0, duration_seconds) * 1000, 1)
        try:
            previous = float(state.get("runtime_latency_ewma_ms"))
        except (TypeError, ValueError):
            previous = latency_ms
        state["runtime_last_latency_ms"] = latency_ms
        state["runtime_latency_ewma_ms"] = round((previous * 0.75) + (latency_ms * 0.25), 1)
    return _write_routing_state(payload, state)


def _write_provider_runtime_failure(
    payload: Any,
    *,
    kind: str,
    error: str,
    occurred_at: datetime,
    duration_seconds: Optional[float],
    cooldown_seconds: int,
) -> tuple[Dict[str, Any], bool]:
    """Record a provider failure without extending an already-open circuit.

    Several in-flight calls can finish after a provider circuit opens. They
    describe the same outage, not new evidence that merits exponentiating the
    provider cooldown. Their model-level telemetry is still persisted by the
    caller, while this shared recovery window remains stable.
    """
    provider_state = _runtime_health_state(payload, occurred_at)
    if provider_state["runtime_failure_active"]:
        existing = dict(payload or {}) if isinstance(payload, dict) else {}
        return existing, True

    last_provider_failure = provider_state["runtime_last_failure_at"]
    recent = bool(
        last_provider_failure
        and occurred_at - last_provider_failure <= _PROVIDER_FAILURE_RECENCY_WINDOW
    )
    state = _extract_routing_state(payload)
    if not recent:
        state["runtime_failure_streak"] = 0
    updated = _write_runtime_failure(
        _write_routing_state(payload, state),
        kind=kind,
        error=error,
        occurred_at=occurred_at,
        duration_seconds=duration_seconds,
        cooldown_seconds=cooldown_seconds,
    )
    updated_state = _runtime_health_state(updated, occurred_at)
    circuit_open = (
        updated_state["runtime_failure_streak"]
        >= _PROVIDER_TIMEOUT_CIRCUIT_THRESHOLD
    )
    if not circuit_open:
        state = _extract_routing_state(updated)
        state.pop("runtime_cooldown_until", None)
        updated = _write_routing_state(updated, state)
    return updated, circuit_open


def _combined_route_rate_limit_state(model: AIModelConfig, provider: AIProviderConfig) -> Dict[str, Any]:
    now = _utcnow()
    model_state = _rate_limit_state(model.extra_params, now)
    provider_state = _rate_limit_state(provider.extra_config, now)

    active_states = []
    if model_state["rate_limit_active"]:
        active_states.append(("model", model_state))
    if provider_state["rate_limit_active"]:
        active_states.append(("provider", provider_state))

    chosen_scope = None
    chosen_state: Optional[Dict[str, Any]] = None
    if active_states:
        chosen_scope, chosen_state = max(
            active_states,
            key=lambda item: item[1]["rate_limit_remaining_seconds"],
        )

    last_model_rate_limit_at = model_state["last_rate_limit_at_iso"] if model_state["rate_limit_count"] > 0 else None
    last_provider_rate_limit_at = provider_state["last_rate_limit_at_iso"] if provider_state["rate_limit_count"] > 0 else None

    return {
        "rate_limit_active": bool(chosen_state),
        "rate_limit_scope": chosen_scope,
        "rate_limit_cooldown_until": chosen_state["cooldown_until_iso"] if chosen_state else None,
        "rate_limit_remaining_seconds": chosen_state["rate_limit_remaining_seconds"] if chosen_state else 0,
        "rate_limit_count": chosen_state["rate_limit_count"] if chosen_state else model_state["rate_limit_count"],
        "last_rate_limit_at": (
            chosen_state["last_rate_limit_at_iso"]
            if chosen_state
            else last_model_rate_limit_at
        ),
        "model_rate_limit_count": model_state["rate_limit_count"],
        "provider_rate_limit_count": provider_state["rate_limit_count"],
        "model_last_rate_limit_at": last_model_rate_limit_at,
        "provider_last_rate_limit_at": last_provider_rate_limit_at,
    }


def _combined_route_runtime_health_state(
    model: AIModelConfig,
    provider: AIProviderConfig,
) -> Dict[str, Any]:
    """Combine model and provider production-call health into route metadata."""
    now = _utcnow()
    model_state = _runtime_health_state(model.extra_params, now)
    provider_state = _runtime_health_state(provider.extra_config, now)
    active_states = [
        ("model", model_state),
        ("provider", provider_state),
    ]
    active_states = [item for item in active_states if item[1]["runtime_failure_active"]]
    scope: Optional[str] = None
    active: Optional[Dict[str, Any]] = None
    if active_states:
        scope, active = max(
            active_states,
            key=lambda item: item[1]["runtime_failure_remaining_seconds"],
        )

    latency = model_state["runtime_latency_ewma_ms"]
    failure_streak = max(
        model_state["runtime_failure_streak"],
        provider_state["runtime_failure_streak"],
    )
    degraded_scope: Optional[str] = None
    degraded_reason: Optional[str] = None
    if model_state["runtime_failure_streak"] >= _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD:
        degraded_scope = "model"
        degraded_reason = "chronic_model_failure_streak"

    return {
        "runtime_failure_active": bool(active),
        "runtime_failure_scope": scope,
        "runtime_failure_cooldown_until": (
            active["runtime_failure_cooldown_until"] if active else None
        ),
        "runtime_failure_remaining_seconds": (
            active["runtime_failure_remaining_seconds"] if active else 0
        ),
        "runtime_failure_kind": (
            active["runtime_failure_kind"] if active else model_state["runtime_failure_kind"]
        ),
        "runtime_failure_streak": failure_streak,
        "runtime_failure_count": model_state["runtime_failure_count"],
        "runtime_timeout_count": model_state["runtime_timeout_count"],
        "runtime_success_count": model_state["runtime_success_count"],
        "runtime_degraded": degraded_scope is not None,
        "runtime_degraded_scope": degraded_scope,
        "runtime_degraded_reason": degraded_reason,
        "runtime_latency_ewma_ms": latency,
        "runtime_last_latency_ms": model_state["runtime_last_latency_ms"],
        "runtime_last_failure_at": (
            active["runtime_last_failure_at"].isoformat()
            if active and active["runtime_last_failure_at"]
            else (
                model_state["runtime_last_failure_at"].isoformat()
                if model_state["runtime_last_failure_at"]
                else None
            )
        ),
        "runtime_last_success_at": (
            model_state["runtime_last_success_at"].isoformat()
            if model_state["runtime_last_success_at"]
            else None
        ),
        "runtime_last_error": (
            active["runtime_last_error"] if active else model_state["runtime_last_error"]
        ),
    }


def _route_sort_key(route: Dict[str, Any]) -> tuple[int, int, float, str]:
    """Keep user priority first, then prefer proven low-latency healthy routes."""
    try:
        priority = int(route.get("priority") or 100)
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


def _positive_int(value: Any, default: int, *, maximum: int = 64) -> int:
    """Read a bounded positive runtime setting from a model-center payload."""
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _runtime_admission_settings(route: Dict[str, Any]) -> Dict[str, int]:
    """Resolve request limits owned by the Model Center.

    Provider limits protect a shared credential or personal-plan quota. Model
    limits keep one slow route from absorbing that provider's entire budget.
    Both can be adjusted through Model Center config; defaults start
    conservatively and increase only after successful live completions.
    """
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    model_params = route.get("extra_params")
    model_params = model_params if isinstance(model_params, dict) else {}

    provider_maximum = _positive_int(
        provider_config.get("runtime_max_concurrency"),
        _DEFAULT_PROVIDER_ADMISSION_MAX_CONCURRENCY,
    )
    provider_minimum = min(
        provider_maximum,
        _positive_int(provider_config.get("runtime_min_concurrency"), 1),
    )
    model_maximum = _positive_int(
        model_params.get("runtime_max_concurrency"),
        _DEFAULT_MODEL_ADMISSION_MAX_CONCURRENCY,
    )
    model_minimum = min(
        model_maximum,
        _positive_int(model_params.get("runtime_min_concurrency"), 1),
    )
    return {
        "provider_minimum": provider_minimum,
        "provider_maximum": provider_maximum,
        "model_minimum": model_minimum,
        "model_maximum": model_maximum,
        "successes_to_scale_up": _positive_int(
            provider_config.get("runtime_successes_to_scale_up"),
            _DEFAULT_ADMISSION_SUCCESS_SCALE_UP,
            maximum=100,
        ),
    }


def _runtime_admission_keys(route: Dict[str, Any]) -> tuple[str, str]:
    provider_key = str(route.get("provider_id") or route.get("provider_key") or "provider").strip()
    model_key = str(route.get("model_id") or route.get("model_key") or route.get("model_name") or "model").strip()
    return provider_key or "provider", model_key or "model"


class RuntimeRouteAdmissionLease:
    """A single Model Center request permit, released exactly once."""

    def __init__(
        self,
        controller: "RuntimeRouteAdmissionController",
        provider_key: str,
        model_key: str,
        settings: Dict[str, int],
    ) -> None:
        self._controller = controller
        self._provider_key = provider_key
        self._model_key = model_key
        self._settings = settings
        self._released = False

    async def release(self, *, success: bool) -> None:
        if self._released:
            return
        self._released = True
        await self._controller.release(
            self._provider_key,
            self._model_key,
            self._settings,
            success=success,
        )


class RuntimeRouteAdmissionController:
    """Process-wide request gate shared by all Model Center completions.

    The dashboard worker is a singleton, so this gate covers every live model
    request without treating task count as a proxy for API concurrency. Durable
    route failures remain in routing_state; in-memory permits reset on restart.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._condition: asyncio.Condition | None = None
        self._provider_inflight: Dict[str, int] = {}
        self._model_inflight: Dict[str, int] = {}
        self._provider_capacity: Dict[str, int] = {}
        self._provider_success_streak: Dict[str, int] = {}

    def _ensure_loop(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._condition is None:
            self._loop = loop
            self._condition = asyncio.Condition()
            self._provider_inflight = {}
            self._model_inflight = {}
            self._provider_capacity = {}
            self._provider_success_streak = {}
        return self._condition

    def score(self, route: Dict[str, Any]) -> tuple[float, float]:
        """Return live pressure for fair candidate ordering without a DB read."""
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        provider_capacity = self._provider_capacity.get(provider_key, settings["provider_minimum"])
        provider_capacity = max(settings["provider_minimum"], min(provider_capacity, settings["provider_maximum"]))
        provider_load = self._provider_inflight.get(provider_key, 0) / provider_capacity
        model_load = self._model_inflight.get(model_key, 0) / settings["model_maximum"]
        # Keep equal-load routes in the Model Center priority order.
        return max(provider_load, model_load), provider_load

    def snapshot(self, route: Dict[str, Any]) -> Dict[str, int]:
        """Expose the active request budget for the Model Center control plane."""
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        provider_capacity = self._provider_capacity.get(provider_key, settings["provider_minimum"])
        provider_capacity = max(settings["provider_minimum"], min(provider_capacity, settings["provider_maximum"]))
        return {
            "runtime_provider_capacity": provider_capacity,
            "runtime_provider_inflight": self._provider_inflight.get(provider_key, 0),
            "runtime_model_capacity": settings["model_maximum"],
            "runtime_model_inflight": self._model_inflight.get(model_key, 0),
        }

    async def acquire(self, route: Dict[str, Any]) -> RuntimeRouteAdmissionLease:
        """Wait for a permit without treating local backpressure as model failure.

        A permit is released whenever the in-flight provider call completes,
        times out, or is cancelled.  Waiting here is therefore the correct
        backpressure mechanism; imposing an unrelated queue deadline made a
        healthy but busy provider look like it had timed out upstream.
        """
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        condition = self._ensure_loop()

        async with condition:
            while True:
                provider_capacity = self._provider_capacity.get(provider_key, settings["provider_minimum"])
                provider_capacity = max(
                    settings["provider_minimum"],
                    min(provider_capacity, settings["provider_maximum"]),
                )
                self._provider_capacity[provider_key] = provider_capacity
                provider_inflight = self._provider_inflight.get(provider_key, 0)
                model_inflight = self._model_inflight.get(model_key, 0)
                if provider_inflight < provider_capacity and model_inflight < settings["model_maximum"]:
                    self._provider_inflight[provider_key] = provider_inflight + 1
                    self._model_inflight[model_key] = model_inflight + 1
                    return RuntimeRouteAdmissionLease(self, provider_key, model_key, settings)

                # Cancellation remains cooperative: worker shutdown and task
                # cancellation can interrupt this wait immediately.  A normal
                # capacity wait must not be surfaced as an API timeout.
                await condition.wait()

    async def release(
        self,
        provider_key: str,
        model_key: str,
        settings: Dict[str, int],
        *,
        success: bool,
    ) -> None:
        condition = self._ensure_loop()
        async with condition:
            self._provider_inflight[provider_key] = max(0, self._provider_inflight.get(provider_key, 1) - 1)
            self._model_inflight[model_key] = max(0, self._model_inflight.get(model_key, 1) - 1)
            current = self._provider_capacity.get(provider_key, settings["provider_minimum"])
            if success:
                streak = self._provider_success_streak.get(provider_key, 0) + 1
                self._provider_success_streak[provider_key] = streak
                if current < settings["provider_maximum"] and streak >= settings["successes_to_scale_up"]:
                    self._provider_capacity[provider_key] = current + 1
                    self._provider_success_streak[provider_key] = 0
                    logger.info(
                        "Model Center admission increased provider={} from {} to {}",
                        provider_key,
                        current,
                        current + 1,
                    )
            else:
                self._provider_success_streak[provider_key] = 0
                self._provider_capacity[provider_key] = settings["provider_minimum"]
            condition.notify_all()


runtime_route_admission = RuntimeRouteAdmissionController()


def runtime_route_admission_score(route: Dict[str, Any]) -> tuple[float, float]:
    """Expose current request pressure for BaseAgent candidate ordering."""
    return runtime_route_admission.score(route)


def runtime_route_admission_snapshot(route: Dict[str, Any]) -> Dict[str, int]:
    """Expose request admission budget for runtime API and dashboard views."""
    return runtime_route_admission.snapshot(route)


async def acquire_runtime_route_admission(route: Dict[str, Any]) -> RuntimeRouteAdmissionLease:
    """Acquire a provider-and-model permit before issuing a live model request."""
    return await runtime_route_admission.acquire(route)


def get_provider_rate_limit_state(provider: AIProviderConfig) -> Dict[str, Any]:
    state = _rate_limit_state(provider.extra_config)
    return {
        "rate_limit_active": state["rate_limit_active"],
        "rate_limit_cooldown_until": state["cooldown_until_iso"],
        "rate_limit_remaining_seconds": state["rate_limit_remaining_seconds"],
        "rate_limit_count": state["rate_limit_count"],
        "last_rate_limit_at": state["last_rate_limit_at_iso"],
    }


def get_provider_runtime_health_state(provider: AIProviderConfig) -> Dict[str, Any]:
    return _runtime_health_state(provider.extra_config)


def get_model_rate_limit_state(model: AIModelConfig, provider: Optional[AIProviderConfig] = None) -> Dict[str, Any]:
    if provider is not None:
        return _combined_route_rate_limit_state(model, provider)

    state = _rate_limit_state(model.extra_params)
    return {
        "rate_limit_active": state["rate_limit_active"],
        "rate_limit_scope": "model" if state["rate_limit_active"] else None,
        "rate_limit_cooldown_until": state["cooldown_until_iso"],
        "rate_limit_remaining_seconds": state["rate_limit_remaining_seconds"],
        "rate_limit_count": state["rate_limit_count"],
        "last_rate_limit_at": state["last_rate_limit_at_iso"],
    }


def get_model_runtime_health_state(
    model: AIModelConfig,
    provider: Optional[AIProviderConfig] = None,
) -> Dict[str, Any]:
    if provider is not None:
        return _combined_route_runtime_health_state(model, provider)
    return _runtime_health_state(model.extra_params)


def _infer_provider_from_model(model_name: str) -> Optional[str]:
    model_lower = (model_name or "").lower()
    if any(k in model_lower for k in ["glm", "chatglm", "zhipu"]):
        return "glm"
    if any(k in model_lower for k in ["qwen", "qianwen"]):
        return "qianwen"
    if any(k in model_lower for k in ["claude", "anthropic"]):
        return "anthropic"
    if any(k in model_lower for k in ["gpt-4", "gpt-3.5", "text-davinci"]):
        return "openai"
    if "azure" in model_lower:
        return "azure"
    return None


def mask_api_key(api_key: Optional[str]) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


async def ensure_model_center_tables() -> None:
    """Create model-center tables if they do not exist."""
    global _schema_ready_engine
    engine = get_engine()
    if _schema_ready_engine is engine:
        return

    async with engine.begin() as conn:
        await conn.run_sync(
            AIProviderConfig.metadata.create_all,
            tables=[AIProviderConfig.__table__, AIModelConfig.__table__],
        )
    _schema_ready_engine = engine
    logger.info("AI model center tables ensured")


def invalidate_model_center_bootstrap_cache() -> None:
    """Force the next non-forced bootstrap call to verify database state.

    Normal callers do not need this: the cache expires after a short interval,
    so a database cleared out-of-band is detected and reseeded.  Tests and code
    that intentionally clear model-center rows can invalidate it immediately.
    No environment values or credentials are retained in this cache.
    """

    global _bootstrap_checked_engine, _bootstrap_checked_at
    _bootstrap_checked_engine = None
    _bootstrap_checked_at = None


def _bootstrap_cache_is_fresh(engine: Any) -> bool:
    return bool(
        _bootstrap_checked_engine is engine
        and _bootstrap_checked_at is not None
        and time.monotonic() - _bootstrap_checked_at < _BOOTSTRAP_RECHECK_SECONDS
    )


def _mark_bootstrap_cache_checked(engine: Any) -> None:
    global _bootstrap_checked_engine, _bootstrap_checked_at
    _bootstrap_checked_engine = engine
    _bootstrap_checked_at = time.monotonic()


def _bootstrap_lock() -> asyncio.Lock:
    """Return a lock scoped to the current event loop.

    Loop scoping keeps application processes single-flight while avoiding a
    lock bound to a closed pytest/application lifecycle loop.
    """

    loop = asyncio.get_running_loop()
    lock = _bootstrap_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _bootstrap_locks[loop] = lock
    return lock


def _env_provider_seed() -> List[Dict[str, Any]]:
    cfg = get_config().ai

    providers: List[Dict[str, Any]] = []

    if cfg.openai_api_key:
        providers.append(
            {
                "provider_key": "openai-default",
                "provider_name": "openai",
                "display_name": "OpenAI (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.openai_base_url,
                "api_key": cfg.openai_api_key,
                "priority": 100,
            }
        )

    if cfg.anthropic_api_key:
        providers.append(
            {
                "provider_key": "anthropic-default",
                "provider_name": "anthropic",
                "display_name": "Anthropic (env)",
                "api_style": "anthropic",
                "base_url": None,
                "api_key": cfg.anthropic_api_key,
                "priority": 100,
            }
        )

    if cfg.glm_api_key:
        providers.append(
            {
                "provider_key": "glm-default",
                "provider_name": "glm",
                "display_name": "GLM (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.glm_base_url,
                "api_key": cfg.glm_api_key,
                "priority": 100,
            }
        )

    if cfg.qianwen_api_key:
        providers.append(
            {
                "provider_key": "qianwen-default",
                "provider_name": "qianwen",
                "display_name": "Qianwen (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.qianwen_base_url,
                "api_key": cfg.qianwen_api_key,
                "priority": 100,
            }
        )

    if cfg.azure_api_key and cfg.azure_endpoint:
        providers.append(
            {
                "provider_key": "azure-default",
                "provider_name": "azure",
                "display_name": "Azure OpenAI (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.azure_endpoint,
                "api_key": cfg.azure_api_key,
                "priority": 100,
                "extra_config": {"api_version": cfg.azure_api_version},
            }
        )

    if cfg.custom_api_key and cfg.custom_base_url:
        providers.append(
            {
                "provider_key": "custom-default",
                "provider_name": "custom",
                "display_name": "Custom (env)",
                "api_style": "openai_compatible",
                "base_url": cfg.custom_base_url,
                "api_key": cfg.custom_api_key,
                "priority": 100,
            }
        )

    return providers


async def _bootstrap_model_center_from_env_uncached(force: bool = False) -> None:
    async with get_db() as db:
        if force:
            for model in (await db.execute(select(AIModelConfig))).scalars().all():
                await db.delete(model)
            for provider in (await db.execute(select(AIProviderConfig))).scalars().all():
                await db.delete(provider)
            await db.commit()

        existing_provider_id = (
            await db.execute(select(AIProviderConfig.id).limit(1))
        ).scalar_one_or_none()
        if not force and existing_provider_id is not None:
            logger.debug("Model center already initialized; env bootstrap skipped")
            return

        providers = _env_provider_seed()
        if not providers:
            logger.debug("No provider credentials in env; skip model-center bootstrap")
            return

        provider_by_name: Dict[str, AIProviderConfig] = {}
        provider_by_key: Dict[str, AIProviderConfig] = {
            provider.provider_key: provider
            for provider in (await db.execute(select(AIProviderConfig))).scalars().all()
        }
        for i, item in enumerate(providers, start=1):
            provider = provider_by_key.get(item["provider_key"])
            if provider is None:
                provider = AIProviderConfig(
                    provider_key=item["provider_key"],
                    provider_name=item["provider_name"],
                    display_name=item["display_name"],
                    api_style=item.get("api_style", "openai_compatible"),
                    base_url=item.get("base_url"),
                    api_key=item.get("api_key"),
                    priority=i * 10,
                    extra_config=item.get("extra_config", {}),
                    is_active=True,
                    last_check_status="unknown",
                )
                db.add(provider)
            else:
                provider.provider_name = provider.provider_name or item["provider_name"]
                provider.display_name = provider.display_name or item["display_name"]
                provider.api_style = provider.api_style or item.get("api_style", "openai_compatible")
                provider.base_url = provider.base_url or item.get("base_url")
                provider.api_key = provider.api_key or item.get("api_key")
                provider.extra_config = provider.extra_config or item.get("extra_config", {})
                provider.is_active = True
            provider_by_name[provider.provider_name] = provider

        await db.flush()

        ai_cfg = get_config().ai
        chain = ai_cfg.model_chain

        if not chain:
            logger.info("No model chain in env; model-center providers seeded without models")
            await db.commit()
            return

        existing_model_keys = {
            model.model_key
            for model in (await db.execute(select(AIModelConfig))).scalars().all()
        }

        route_specs: List[tuple[str, str]] = []
        for model_name in chain:
            route_specs.append((_infer_provider_from_model(model_name) or ai_cfg.default_provider, model_name))

        represented_providers = {provider_name for provider_name, _ in route_specs}
        default_provider = ai_cfg.default_provider
        if default_provider in provider_by_name and default_provider not in represented_providers:
            for fallback_name in (ai_cfg.default_model, ai_cfg.fallback_model):
                if fallback_name and (default_provider, fallback_name) not in route_specs:
                    route_specs.append((default_provider, fallback_name))

        provider_default_models: Dict[str, List[str]] = {
            "glm": [
                ai_cfg.default_model if _infer_provider_from_model(ai_cfg.default_model) == "glm" else "glm-4-7",
                ai_cfg.fallback_model if _infer_provider_from_model(ai_cfg.fallback_model) == "glm" else "glm-4-plus",
            ],
        }
        for provider_name, model_names in provider_default_models.items():
            if provider_name not in provider_by_name or provider_name in represented_providers:
                continue
            for model_name in model_names:
                if model_name and (provider_name, model_name) not in route_specs:
                    route_specs.append((provider_name, model_name))

        for idx, (preferred_provider, model_name) in enumerate(route_specs, start=1):
            provider = provider_by_name.get(preferred_provider)
            if provider is None and provider_by_name:
                provider = list(provider_by_name.values())[0]
            if provider is None:
                continue

            model_key = f"{provider.provider_key}:{model_name}"
            if model_key in existing_model_keys:
                continue

            model = AIModelConfig(
                provider_id=provider.id,
                model_key=model_key,
                model_name=model_name,
                display_name=model_name,
                api_style=None,
                priority=idx * 10,
                is_enabled=True,
                is_default=(idx == 1),
                last_check_status="unknown",
            )
            db.add(model)

        await db.commit()
        logger.info("Model center bootstrapped from env")


async def bootstrap_model_center_from_env(force: bool = False) -> None:
    """Seed provider/model records from env when needed.

    Non-forced calls are process-locally cached for a short interval and
    concurrent callers share one database verification.  The finite cache
    still detects an out-of-band database clear, while ``force=True`` always
    bypasses it and retains the explicit destructive rebuild behavior.
    """

    engine = get_engine()
    if force:
        # Make concurrent non-forced callers join the forced rebuild instead
        # of observing a stale successful check while rows are being replaced.
        invalidate_model_center_bootstrap_cache()
    elif _bootstrap_cache_is_fresh(engine):
        return

    async with _bootstrap_lock():
        if not force and _bootstrap_cache_is_fresh(engine):
            return

        await ensure_model_center_tables()
        await _bootstrap_model_center_from_env_uncached(force=force)
        _mark_bootstrap_cache_checked(engine)


def is_rate_limit_error(error: Any) -> bool:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    if normalized_status == 429:
        return True

    # Some providers return throttling as HTTP 400 with provider-specific error code.
    code_candidates = _extract_error_codes(error)
    message = str(error)

    if any(code in {"429", "2003", "insufficient_quota", "quota_exceeded", "rate_limit_exceeded"} for code in code_candidates):
        return True

    msg = message.lower()
    return any(
        keyword in msg
        for keyword in [
            "insufficient_quota",
            "rate limit",
            "429",
            "quota",
            "too many requests",
            "too frequent",
            "request frequency",
            "throttl",
            "retry later",
            "allocationquota",
            "free tier",
            "\u8bf7\u6c42\u9650\u9891",  # 请求限频
            "\u9650\u9891",  # 限频
            "\u9891\u7387\u9650\u5236",  # 频率限制
            "\u7a0d\u540e\u91cd\u8bd5",  # 稍后重试
        ]
    )


def _extract_error_codes(error: Any) -> List[str]:
    codes: List[str] = []

    direct_code = getattr(error, "code", None)
    if direct_code is not None:
        codes.append(str(direct_code).strip().lower())

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        body_code = body.get("code")
        if body_code is not None:
            codes.append(str(body_code).strip().lower())

        nested = body.get("error")
        if isinstance(nested, dict):
            nested_code = nested.get("code")
            if nested_code is not None:
                codes.append(str(nested_code).strip().lower())

    message = str(error)
    code_match = re.search(r"['\"]code['\"]\s*:\s*['\"]?([A-Za-z0-9_-]+)", message)
    if code_match:
        codes.append(code_match.group(1).strip().lower())

    return [c for c in codes if c]


def is_model_unavailable_error(error: Any) -> bool:
    """Detect unrecoverable model-level errors (missing model or no access)."""
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    code_candidates = _extract_error_codes(error)
    if any(code in {"model_not_found", "invalid_model", "model_access_denied"} for code in code_candidates):
        return True

    msg = str(error).lower()
    message_hit = any(
        keyword in msg
        for keyword in [
            "model not found",
            "model does not exist",
            "does not exist or you do not have access",
            "you do not have access to it",
            "invalid model",
            "unsupported model",
            "unknown model",
        ]
    )

    # 404 + model-related message usually means route/model is invalid for this provider key.
    if normalized_status == 404 and message_hit:
        return True

    return message_hit


def extract_retry_after_seconds(error: Any) -> Optional[int]:
    headers = None
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(error, "headers", None)

    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(1, int(float(str(retry_after).strip())))
            except (TypeError, ValueError):
                pass

    message = str(error)
    patterns = [
        r"retry after\s+(\d+(?:\.\d+)?)s",
        r"try again in\s+(\d+(?:\.\d+)?)s",
        r"in\s+(\d+(?:\.\d+)?)\s*seconds",
        r"after\s+(\d+(?:\.\d+)?)\s*seconds",
        r"(?:please\s*)?retry\s*(?:in\s*)?(\d+(?:\.\d+)?)\s*s",
        r"(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u518d\u8bd5|\u91cd\u8bd5)",
        r"\u8bf7\u5728\s*(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u91cd\u8bd5|\u518d\u8bd5)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            try:
                return max(1, int(float(match.group(1))))
            except (TypeError, ValueError):
                continue
    return None


async def get_runtime_routes() -> List[Dict[str, Any]]:
    """Return all enabled runtime routes with routing availability metadata."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        rows = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .join(AIProviderConfig, AIModelConfig.provider_id == AIProviderConfig.id)
                .where(
                    AIModelConfig.is_enabled.is_(True),
                    AIProviderConfig.is_active.is_(True),
                )
                .order_by(AIModelConfig.priority.asc(), AIProviderConfig.priority.asc())
            )
        ).scalars().all()

        routes: List[Dict[str, Any]] = []
        for model in rows:
            provider = model.provider
            if not provider:
                continue

            rate_limit_state = _combined_route_rate_limit_state(model, provider)
            runtime_health_state = _combined_route_runtime_health_state(model, provider)
            provider_status = str(provider.last_check_status or "").strip().lower()
            model_status = str(model.last_check_status or "").strip().lower()
            status_routable = provider_status != "unavailable" and model_status != "unavailable"

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
                    "max_tokens": model.max_tokens,
                    "has_api_key": bool(provider.api_key),
                    "last_check_status": model.last_check_status or provider.last_check_status,
                    "available_for_routing": bool(provider.api_key)
                    and status_routable
                    and not rate_limit_state["rate_limit_active"]
                    and not runtime_health_state["runtime_failure_active"]
                    and not runtime_health_state["runtime_degraded"],
                    **rate_limit_state,
                    **runtime_health_state,
            }
            route.update(runtime_route_admission_snapshot(route))
            routes.append(route)

        return sorted(routes, key=_route_sort_key)


async def get_active_model_routes() -> List[Dict[str, Any]]:
    """Return routable model routes ordered by model priority."""
    routes = await get_runtime_routes()
    return [route for route in routes if route.get("available_for_routing")]


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _openai_response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return _content_to_text(getattr(message, "content", None))
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text

    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return _content_to_text(message.get("content"))
                text = first.get("text")
                if isinstance(text, str):
                    return text
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        return json.dumps(response, ensure_ascii=False)

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    return str(response)


def _looks_like_html(text: str) -> bool:
    normalized = text.lstrip().lower()
    return normalized.startswith("<!doctype html") or normalized.startswith("<html")


def _test_max_tokens(route: Dict[str, Any]) -> int:
    configured = route.get("max_tokens")
    try:
        value = int(configured) if configured is not None else 512
    except (TypeError, ValueError):
        value = 512
    return max(64, min(value, 1600))


def _response_preview(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def _model_ids_from_catalogue(payload: Any) -> List[str]:
    """Extract stable model identifiers from OpenAI-compatible model listings."""
    items = getattr(payload, "data", None)
    if isinstance(payload, dict):
        items = payload.get("data", items)
    if not isinstance(items, list):
        return []

    model_ids: List[str] = []
    for item in items:
        identifier = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        value = str(identifier or "").strip()
        if value and value not in model_ids:
            model_ids.append(value)
    return sorted(model_ids)


async def discover_provider_models_by_id(provider_id: int) -> Dict[str, Any]:
    """Read a provider's API model catalogue without altering routing state.

    Catalogue access proves credentials and endpoint reachability only. Routes
    become available exclusively after a structured completion probe succeeds.
    """
    await bootstrap_model_center_from_env(force=False)
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return {"success": False, "status": "not_found", "message": "Provider not found", "models": []}
        if not provider.api_key:
            return {"success": False, "status": "invalid", "message": "API key is not configured", "models": []}
        if str(provider.api_style or "openai_compatible").lower() == "anthropic":
            return {"success": False, "status": "unsupported", "message": "Anthropic does not expose an OpenAI-compatible model catalogue", "models": []}
        base_url = str(provider.base_url or "").rstrip("/")
        api_key = provider.api_key
        headers = provider.extra_headers or {}

    candidates = [base_url or None]
    if base_url and not base_url.endswith("/v1"):
        candidates.append(f"{base_url}/v1")
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=candidate, default_headers=headers or None)
            models = _model_ids_from_catalogue(await client.models.list())
            message = f"Discovered {len(models)} API model(s)"
            async with get_db() as db:
                provider = await db.get(AIProviderConfig, provider_id)
                if provider is not None:
                    config = dict(provider.extra_config or {})
                    config["model_discovery"] = {"status": "available", "count": len(models), "checked_at": _utcnow().isoformat()}
                    provider.extra_config = config
                    await db.commit()
            return {"success": True, "status": "available", "message": message, "models": models, "base_url": candidate or "default-openai-base-url"}
        except Exception as exc:
            last_error = exc
            if is_rate_limit_error(exc) or getattr(exc, "status_code", None) in {400, 401, 403, 429}:
                break
    return {"success": False, "status": "rate_limited" if is_rate_limit_error(last_error) else "unavailable", "message": str(last_error or "Model catalogue request failed"), "models": []}


def _validate_test_response(text: str) -> None:
    value = text.strip()
    if not value:
        raise RuntimeError("Chat completion returned an empty assistant response")
    if _looks_like_html(value):
        raise RuntimeError(
            "Chat completion returned an HTML page instead of an assistant response. "
            "Check the provider base_url; OpenAI-compatible gateways usually require /v1."
        )
    if _MODEL_TEST_MARKER not in value.lower():
        raise RuntimeError(f"Chat completion did not echo the expected test marker. Response preview: {_response_preview(value)}")


def _validate_structured_test_response(text: str) -> None:
    value = text.strip()
    if not value:
        raise RuntimeError("Structured workload probe returned an empty assistant response")
    if _looks_like_html(value):
        raise RuntimeError("Structured workload probe returned HTML instead of JSON")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Structured workload probe did not return valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "globalid-structured-probe-ok":
        raise RuntimeError("Structured workload probe returned an unexpected JSON payload")
    items = payload.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise RuntimeError("Structured workload probe omitted its required items array")


async def _test_openai_compatible(
    route: Dict[str, Any], model_name: str, *, structured: bool = False
) -> Dict[str, Any]:
    base_url = str(route.get("base_url") or "").rstrip("/")
    base_urls: List[Optional[str]] = [base_url or None]
    if base_url and not base_url.endswith("/v1"):
        base_urls.append(f"{base_url}/v1")

    last_error: Optional[Exception] = None
    for candidate_base_url in base_urls:
        try:
            client = AsyncOpenAI(
                api_key=route.get("api_key"),
                base_url=candidate_base_url,
                default_headers=(route.get("extra_headers") or None),
            )
            prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are validating that this chat model can answer normal conversations.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max(128, min(_test_max_tokens(route), 512)) if structured else _test_max_tokens(route),
                temperature=0,
            )
            text = _openai_response_text(response)
            if structured:
                _validate_structured_test_response(text)
            else:
                _validate_test_response(text)
            return {
                "base_url": candidate_base_url or "default-openai-base-url",
                "response_preview": _response_preview(text),
            }
        except Exception as exc:
            last_error = exc
            if is_rate_limit_error(exc) or getattr(exc, "status_code", None) in {400, 401, 403, 429}:
                raise
            continue

    if last_error is not None:
        raise last_error
    raise RuntimeError("Chat completion returned no usable response")


async def _test_anthropic(
    route: Dict[str, Any], model_name: str, *, structured: bool = False
) -> Dict[str, Any]:
    prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
    client = AsyncAnthropic(api_key=route.get("api_key"))
    response = await client.messages.create(
        model=model_name,
        system="You are validating that this chat model can answer normal conversations.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max(128, min(_test_max_tokens(route), 512)) if structured else _test_max_tokens(route),
        temperature=0,
    )
    text = "\n".join(
        str(block.text)
        for block in response.content
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    )
    if structured:
        _validate_structured_test_response(text)
    else:
        _validate_test_response(text)
    return {"response_preview": _response_preview(text)}


async def test_route_connection(
    route: Dict[str, Any], *, structured: bool = False
) -> Dict[str, Any]:
    """Test one model route with a connection or structured-workload probe."""
    style = str(route.get("api_style") or "openai_compatible").lower()
    model_name = str(route.get("model_name") or "")

    try:
        if not route.get("api_key"):
            raise RuntimeError("API key is not configured")

        if style == "anthropic":
            details = await _test_anthropic(route, model_name, structured=structured)
        else:
            details = await _test_openai_compatible(route, model_name, structured=structured)

        probe_name = "Structured workload probe" if structured else "Chat completion test"
        marker = "globalid-structured-probe-ok" if structured else _MODEL_TEST_MARKER
        message = f"{probe_name} successful. Response: {details.get('response_preview') or marker}"

        return {
            "success": True,
            "status": "available",
            "message": message,
            "model_name": model_name,
            "test_type": "structured_workload" if structured else "chat_completion",
            "test_prompt": _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT,
            **details,
        }
    except Exception as exc:
        return {
            "success": False,
            "status": "rate_limited" if is_rate_limit_error(exc) else "unavailable",
            "message": str(exc),
            "model_name": model_name,
            "test_type": "structured_workload" if structured else "chat_completion",
            "test_prompt": _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT,
            "retry_after_seconds": extract_retry_after_seconds(exc),
        }


async def update_model_check_result(model_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id)
        if model is None:
            return
        model.last_check_status = status
        model.last_check_message = message
        model.last_checked_at = _utcnow()
        if status == "available":
            model.extra_params = _write_runtime_success(
                _clear_payload_rate_limit(model.extra_params, model.last_checked_at),
                occurred_at=model.last_checked_at,
                duration_seconds=None,
            )
        await db.commit()


async def update_provider_check_result(provider_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return
        provider.last_check_status = status
        provider.last_check_message = message
        provider.last_checked_at = _utcnow()
        if status == "available":
            provider.extra_config = _write_runtime_success(
                _clear_payload_rate_limit(provider.extra_config, provider.last_checked_at),
                occurred_at=provider.last_checked_at,
                duration_seconds=None,
            )
        await db.commit()


async def mark_route_rate_limited(
    route: Dict[str, Any],
    message: str,
    retry_after_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist provider/model cooldown after a runtime rate-limit error."""
    route_extra_params = route.get("extra_params") or {}
    route_extra_config = route.get("extra_config") or {}
    mark_provider_raw = route_extra_config.get("mark_provider_rate_limited", False)
    if isinstance(mark_provider_raw, str):
        mark_provider_rate_limited = mark_provider_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        mark_provider_rate_limited = bool(mark_provider_raw)
    cooldown_seconds = retry_after_seconds
    if cooldown_seconds is None:
        cooldown_seconds = int(
            route_extra_params.get("rate_limit_cooldown_seconds")
            or route_extra_config.get("rate_limit_cooldown_seconds")
            or get_config().ai.rate_limit_cooldown_seconds
        )

    now = _utcnow()
    cooldown_until = now + timedelta(seconds=max(1, int(cooldown_seconds)))

    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        provider = await db.get(AIProviderConfig, int(route["provider_id"]))

        if model is not None:
            model.last_check_status = "rate_limited"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _mark_payload_rate_limited(model.extra_params, cooldown_until, now)

        if provider is not None:
            if mark_provider_rate_limited:
                provider.last_check_status = "rate_limited"
                provider.last_check_message = message
                provider.last_checked_at = now
                provider.extra_config = _mark_payload_rate_limited(provider.extra_config, cooldown_until, now)
            else:
                # Default behavior: cooldown this model route only. This prevents one throttled model
                # from blocking sibling models under the same provider/API key.
                provider.extra_config = _clear_payload_rate_limit(provider.extra_config, now)
                if str(provider.last_check_status or "").strip().lower() == "rate_limited":
                    provider.last_check_status = "unknown"
                    provider.last_check_message = "Provider cooldown cleared; using model-level cooldown only"
                    provider.last_checked_at = now

        await db.commit()

    return {
        "cooldown_until": cooldown_until.isoformat(),
        "cooldown_seconds": max(1, int(cooldown_seconds)),
    }


async def record_route_runtime_failure(
    route: Dict[str, Any],
    error: BaseException | str,
    *,
    duration_seconds: Optional[float] = None,
    cooldown_seconds: int = 60,
) -> Dict[str, Any]:
    """Persist a real production-call transport failure for future routing.

    Rate-limit and invalid-model failures already have durable specialised
    handlers.  This function owns timeout, connection and transient 5xx
    failures, including a provider circuit after multiple sibling timeouts.
    """
    kind = _runtime_failure_kind(error)
    if kind not in _RUNTIME_FAILURE_KINDS:
        return {"recorded": False, "reason": "not_a_transport_failure"}

    try:
        model_id = int(route.get("model_id"))
        provider_id = int(route.get("provider_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = _utcnow()
    message = str(error or "")[:1000]
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        provider = await db.get(AIProviderConfig, provider_id, with_for_update=True)
        if model is None:
            return {"recorded": False, "reason": "model_not_found"}

        model.extra_params = _write_runtime_failure(
            model.extra_params,
            kind=kind,
            error=message,
            occurred_at=now,
            duration_seconds=duration_seconds,
            cooldown_seconds=cooldown_seconds,
        )
        model_state = _runtime_health_state(model.extra_params, now)

        provider_circuit_open = False
        if provider is not None and kind in {"timeout", "connection"}:
            provider.extra_config, provider_circuit_open = _write_provider_runtime_failure(
                provider.extra_config,
                kind=kind,
                error=message,
                occurred_at=now,
                duration_seconds=duration_seconds,
                cooldown_seconds=cooldown_seconds,
            )

        await db.commit()

    return {
        "recorded": True,
        "kind": kind,
        "cooldown_seconds": model_state["runtime_failure_remaining_seconds"],
        "provider_circuit_open": provider_circuit_open,
    }


async def record_route_runtime_success(
    route: Dict[str, Any],
    *,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist a successful production completion and its observed latency."""
    try:
        model_id = int(route.get("model_id"))
        provider_id = int(route.get("provider_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        provider = await db.get(AIProviderConfig, provider_id, with_for_update=True)
        if model is None:
            return {"recorded": False, "reason": "model_not_found"}

        model.extra_params = _write_runtime_success(
            model.extra_params,
            occurred_at=now,
            duration_seconds=duration_seconds,
        )
        if provider is not None:
            provider.extra_config = _write_runtime_success(
                provider.extra_config,
                occurred_at=now,
                duration_seconds=duration_seconds,
            )
        await db.commit()

    return {"recorded": True}


async def mark_route_unavailable(
    route: Dict[str, Any],
    message: str,
) -> None:
    """Persist model-level unavailable status when provider reports model_not_found/no_access."""
    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        if model is not None:
            model.last_check_status = "unavailable"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)
            await db.commit()


async def mark_model_unavailable_by_name(
    model_name: str,
    message: str,
) -> int:
    """Persist unavailable status by model name when runtime route metadata is missing."""
    normalized = str(model_name or "").strip()
    if not normalized:
        return 0

    now = _utcnow()
    async with get_db() as db:
        models = (
            await db.execute(
                select(AIModelConfig).where(
                    AIModelConfig.model_name == normalized,
                    AIModelConfig.is_enabled.is_(True),
                )
            )
        ).scalars().all()

        if not models:
            return 0

        for model in models:
            model.last_check_status = "unavailable"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)

        await db.commit()
        return len(models)


async def clear_route_rate_limit(route: Dict[str, Any], message: str = "Connection successful") -> None:
    """Clear persisted cooldown after a route has recovered."""
    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))
        provider = await db.get(AIProviderConfig, int(route["provider_id"]))

        if model is not None:
            model.last_check_status = "available"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)

        if provider is not None:
            provider.last_check_status = "available"
            provider.last_check_message = message
            provider.last_checked_at = now
            provider.extra_config = _clear_payload_rate_limit(provider.extra_config, now)

        await db.commit()


async def check_model_by_id(model_id: int, *, structured: bool = True) -> Dict[str, Any]:
    """Test one model route by DB id and persist status.

    The default structured probe catches the common false-positive where a
    provider can echo a short marker but cannot reliably return JSON payloads.
    """
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        model = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .where(AIModelConfig.id == model_id)
            )
        ).scalar_one_or_none()
        if model is None:
            return {"success": False, "status": "not_found", "message": "Model not found"}

        provider = model.provider
        if provider is None:
            return {"success": False, "status": "invalid", "message": "Provider not found"}

        route = {
            "model_id": model.id,
            "model_key": model.model_key,
            "model_name": model.model_name,
            "provider_id": provider.id,
            "provider_key": provider.provider_key,
            "provider_name": provider.provider_name,
            "api_style": model.api_style or provider.api_style,
            "base_url": provider.base_url,
            "api_key": provider.api_key,
            "extra_headers": provider.extra_headers or {},
            "extra_config": provider.extra_config or {},
            "extra_params": model.extra_params or {},
            "temperature": model.temperature,
            "max_tokens": model.max_tokens,
        }

    result = await test_route_connection(route, structured=structured)
    await update_model_check_result(model_id, result["status"], result["message"])
    await update_provider_check_result(route["provider_id"], result["status"], result["message"])
    return result


async def check_provider_by_id(provider_id: int, *, structured: bool = True) -> Dict[str, Any]:
    """Test enabled provider models until one structured call succeeds."""
    await bootstrap_model_center_from_env(force=False)

    async with get_db() as db:
        models = (
            await db.execute(
                select(AIModelConfig)
                .options(selectinload(AIModelConfig.provider))
                .where(AIModelConfig.provider_id == provider_id, AIModelConfig.is_enabled.is_(True))
                .order_by(AIModelConfig.priority.asc())
            )
        ).scalars().all()

    if not models:
        result = {
            "success": False,
            "status": "invalid",
            "message": "No enabled model under this provider",
        }
        await update_provider_check_result(provider_id, result["status"], result["message"])
        return result

    failures: List[str] = []
    for model in models:
        result = await check_model_by_id(model.id, structured=structured)
        if result.get("success"):
            return result
        failures.append(f"{model.model_name}: {result.get('message', 'unavailable')}")
    result = {"success": False, "status": "unavailable", "message": "All enabled provider models failed: " + " | ".join(failures), "models": []}
    await update_provider_check_result(provider_id, result["status"], result["message"])
    return result


async def check_all_models(*, structured: bool = True) -> List[Dict[str, Any]]:
    """Test all enabled runtime routes and persist statuses."""
    routes = await get_runtime_routes()
    results: List[Dict[str, Any]] = []
    for route in routes:
        result = await test_route_connection(route, structured=structured)
        if result["status"] == "rate_limited":
            await mark_route_rate_limited(
                route,
                result["message"],
                retry_after_seconds=result.get("retry_after_seconds"),
            )
        await update_model_check_result(route["model_id"], result["status"], result["message"])
        await update_provider_check_result(route["provider_id"], result["status"], result["message"])
        result.update(
            {
                "model_id": route["model_id"],
                "provider_id": route["provider_id"],
                "provider_key": route["provider_key"],
            }
        )
        results.append(result)
    return results
