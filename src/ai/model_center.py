"""AI model center service: schema bootstrap, routing, and health checks."""

from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core import get_config, get_db, get_engine, get_logger
from src.domain import AIModelCallLog, AIModelConfig, AIProviderConfig

logger = get_logger(__name__)

_schema_ready_engine: Any = None
_bootstrap_checked_engine: Any = None
_bootstrap_checked_at: Optional[float] = None
_bootstrap_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakKeyDictionary()
_BOOTSTRAP_RECHECK_SECONDS = 60.0
_ROUTING_STATE_KEY = "routing_state"
_RUNTIME_FAILURE_KINDS = {"timeout", "connection", "upstream"}
_RUNTIME_FAILURE_COOLDOWN_CAP_SECONDS = 120
_MODEL_TRANSPORT_CIRCUIT_THRESHOLD = 3
_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD = 8
_MODEL_FAILURE_STREAK_DECAY_INTERVAL = timedelta(minutes=30)
_MODEL_RECOVERY_PROBE_INTERVAL_SECONDS = 300
_DEFAULT_PROVIDER_ADMISSION_MAX_CONCURRENCY = 2
_DEFAULT_MODEL_ADMISSION_MAX_CONCURRENCY = 1
_MODEL_CONCURRENCY_CEILING = 512
_MODEL_TEST_MARKER = "globalid-model-test-ok"
_PROVIDER_AUTHENTICATION_FAILURE_MESSAGE = (
    "Provider credentials were rejected. Update the credentials and run a provider "
    "connection test before routing requests again."
)
_MODEL_TEST_PROMPT = (
    "This is a production model-center health check. "
    f"Reply with exactly this text and nothing else: {_MODEL_TEST_MARKER}"
)

_ROUTE_CHANNEL_FAILURE_CODES = {"get_channel_failed"}

_STRUCTURED_MODEL_TEST_PROMPT = (
    "This is a production model-center literature workload probe. Return JSON only. "
    "Use exactly this top-level shape: "
    "{\"status\":\"globalid-structured-probe-ok\",\"items\":[...]}. "
    "Create exactly three items with integer ids 1, 2, and 3. Each item must contain "
    "a title, a factual summary of at least 60 characters, and a confidence value from "
    "0 to 1. Summarize these synthetic findings: (1) wastewater surveillance can lead "
    "clinical case trends; (2) vaccination reduces severe outcomes; (3) reporting delay "
    "biases the newest observations. Do not use markdown or add unsupported facts."
)

_recovery_probe_attempted_at: Dict[int, float] = {}

#: Anthropic rejects a request that omits ``max_tokens``. This is that protocol's
#: ceiling, not a product limit. OpenAI-compatible calls omit the field entirely.
ANTHROPIC_PROTOCOL_MAX_TOKENS = 64000
_DEFAULT_AVAILABILITY_TIMEZONE = "Asia/Shanghai"
_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_THINK_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*?</(?:think|thinking)>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_OPEN_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_OPTIONAL_REJECTION_MARKERS = (
    "unsupported",
    "unknown parameter",
    "invalid parameter",
    "unexpected",
    "not allowed",
    "unrecognized",
    "not supported",
    "is not supported",
    "不支持",
    "未知参数",
)
_PROTECTED_COMPLETION_FIELDS = {"model", "messages"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SECRET_VALUE_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_-]?key\s*[:=]\s*|"
    r"access[_-]?token\s*[:=]\s*|sk-[a-z0-9_-]{8,})[^\s,;]+"
)


async def record_model_call_log(
    *,
    request_id: UUID,
    route: Optional[Dict[str, Any]],
    model_name: str,
    provider_key: str,
    agent_name: str,
    status: str,
    latency_ms: float,
    error: Optional[BaseException] = None,
) -> None:
    """Persist a metadata-only record for one real provider attempt.

    Logging is best effort: a telemetry storage issue must never turn a valid
    model response into an application failure.
    """
    route = route or {}
    error_message = _SECRET_VALUE_RE.sub(r"\1[REDACTED]", str(error))[:1000] if error else None
    try:
        async with get_db() as db:
            db.add(
                AIModelCallLog(
                    request_id=str(request_id),
                    model_id=_safe_int(route.get("model_id")),
                    provider_id=_safe_int(route.get("provider_id")),
                    model_key=str(route.get("model_key") or f"{provider_key}:{model_name}")[:200],
                    model_name=str(model_name)[:120],
                    provider_key=str(route.get("provider_key") or provider_key or "unknown")[:120],
                    agent_name=str(agent_name)[:160],
                    status="success" if status == "success" else "failed",
                    latency_ms=round(max(0.0, float(latency_ms)), 1),
                    error_type=type(error).__name__[:160] if error else None,
                    error_message=error_message,
                )
            )
    except Exception as exc:
        logger.warning("Failed to persist AI model call log: {}", exc)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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

    Even realistic health probes are only point-in-time samples. Keep their
    result separate from production workload health so routing can temporarily
    avoid a route without claiming that its credential is invalid.
    """
    now = now or _utcnow()
    state = _extract_routing_state(payload)
    cooldown_until = _parse_datetime(state.get("runtime_cooldown_until"))
    last_failure_at = _parse_datetime(state.get("last_runtime_failure_at"))
    if cooldown_until and last_failure_at:
        # Apply the current upper bound to persisted cooldowns written by older
        # versions as well; otherwise they can strand every route for 10 min.
        cooldown_until = min(
            cooldown_until,
            last_failure_at + timedelta(seconds=_RUNTIME_FAILURE_COOLDOWN_CAP_SECONDS),
        )
    failure_kind = str(state.get("last_runtime_failure_kind") or "").strip() or None
    # Older versions opened circuits for malformed/empty structured output.
    # Those records are telemetry, not evidence of transport failure.
    active = bool(
        failure_kind in _RUNTIME_FAILURE_KINDS
        and cooldown_until
        and cooldown_until > now
    )

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

    raw_failure_streak = _integer("runtime_failure_streak")
    failure_streak = raw_failure_streak if failure_kind in _RUNTIME_FAILURE_KINDS else 0
    if last_failure_at and now > last_failure_at:
        decay_seconds = max(1, int(_MODEL_FAILURE_STREAK_DECAY_INTERVAL.total_seconds()))
        elapsed_intervals = int((now - last_failure_at).total_seconds() // decay_seconds)
        failure_streak = max(0, failure_streak - elapsed_intervals)

    return {
        "runtime_failure_active": active,
        "runtime_failure_cooldown_until": cooldown_until.isoformat() if active and cooldown_until else None,
        "runtime_failure_remaining_seconds": (
            max(0, int((cooldown_until - now).total_seconds()))
            if active and cooldown_until
            else 0
        ),
        "runtime_failure_kind": failure_kind,
        # The raw count remains durable for observability, while the effective
        # streak loses one strike per quiet interval. A route that recovered
        # upstream therefore cannot remain permanently excluded solely because
        # no production request was allowed through to prove a success.
        "runtime_failure_streak": failure_streak,
        "runtime_failure_streak_raw": raw_failure_streak,
        "runtime_failure_count": _integer("runtime_failure_count"),
        "runtime_timeout_count": _integer("runtime_timeout_count"),
        "runtime_success_count": _integer("runtime_success_count"),
        "runtime_latency_ewma_ms": _number("runtime_latency_ewma_ms"),
        "runtime_last_latency_ms": _number("runtime_last_latency_ms"),
        "runtime_last_failure_at": last_failure_at,
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
    if any(marker in message for marker in ("timeout", "timed out", "readtimeout", "connecttimeout")):
        return "timeout"
    if any(
        marker in message
        for marker in (
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
    current_health = _runtime_health_state(payload, occurred_at)
    # In-flight requests can finish after a route circuit opens. They belong to
    # the same outage and must not keep pushing the recovery time forward.
    previous_streak = current_health["runtime_failure_streak"]
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
        }
    )
    # One failed request is weak evidence: it may be a transient network hop,
    # a caller deadline, or a provider-side interruption. Open a route circuit
    # only after repeated transport failures. The raw failures remain visible
    # in telemetry and a successful request resets the consecutive streak.
    if current_health["runtime_failure_active"]:
        pass
    elif streak >= _MODEL_TRANSPORT_CIRCUIT_THRESHOLD:
        state["runtime_cooldown_until"] = (
            occurred_at
            + timedelta(
                seconds=_runtime_failure_cooldown_seconds(
                    base_seconds=cooldown_seconds,
                    failure_streak=streak,
                )
            )
        ).isoformat()
    else:
        state.pop("runtime_cooldown_until", None)
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


def _combined_route_rate_limit_state(model: AIModelConfig, provider: AIProviderConfig) -> Dict[str, Any]:
    now = _utcnow()
    model_state = _rate_limit_state(model.extra_params, now)
    provider_state = _rate_limit_state(provider.extra_config, now)
    last_model_rate_limit_at = model_state["last_rate_limit_at_iso"] if model_state["rate_limit_count"] > 0 else None
    last_provider_rate_limit_at = provider_state["last_rate_limit_at_iso"] if provider_state["rate_limit_count"] > 0 else None

    return {
        # Ignore legacy provider cooldowns: a model-level 429 must not block
        # healthy sibling routes that share the provider credential.
        "rate_limit_active": model_state["rate_limit_active"],
        "rate_limit_scope": "model" if model_state["rate_limit_active"] else None,
        "rate_limit_cooldown_until": model_state["cooldown_until_iso"],
        "rate_limit_remaining_seconds": model_state["rate_limit_remaining_seconds"],
        "rate_limit_count": model_state["rate_limit_count"],
        "last_rate_limit_at": last_model_rate_limit_at,
        "model_rate_limit_count": model_state["rate_limit_count"],
        "provider_rate_limit_count": provider_state["rate_limit_count"],
        "model_last_rate_limit_at": last_model_rate_limit_at,
        "provider_last_rate_limit_at": last_provider_rate_limit_at,
    }


def _combined_route_runtime_health_state(
    model: AIModelConfig,
    provider: AIProviderConfig,
) -> Dict[str, Any]:
    """Apply production-call runtime health to this model route only.

    A timeout or connection failure may be model-specific even when sibling
    models share a provider. Provider-wide runtime circuits made one failing
    route suppress otherwise healthy sibling routes, so legacy provider
    runtime state is retained for diagnostics but ignored for admission.
    """
    now = _utcnow()
    model_state = _runtime_health_state(model.extra_params, now)
    active = model_state if model_state["runtime_failure_active"] else None
    scope: Optional[str] = "model" if active else None

    latency = model_state["runtime_latency_ewma_ms"]
    failure_streak = model_state["runtime_failure_streak"]
    failure_streak_raw = model_state["runtime_failure_streak_raw"]
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
        "runtime_failure_streak_raw": failure_streak_raw,
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


def _route_routing_status(
    route: Dict[str, Any],
    *,
    status_routable: bool,
    rate_limit_state: Dict[str, Any],
    runtime_health_state: Dict[str, Any],
) -> tuple[str, str]:
    """Describe actual route admission separately from the latest probe result."""
    if not route.get("has_api_key"):
        return "missing_credentials", "Provider API credentials are not configured."
    if rate_limit_state["rate_limit_active"]:
        return "rate_limited", "The route is waiting for its rate-limit cooldown to expire."
    if runtime_health_state["runtime_failure_active"]:
        return "cooling_down", "The route is temporarily cooling down after a production workload failure."
    if runtime_health_state["runtime_degraded"]:
        return "degraded", "The route is excluded after repeated production failures and is awaiting an automatic recovery probe."
    if not status_routable:
        return "probe_unavailable", "The latest model or provider workload probe did not succeed."
    return "routable", "The route passed admission checks and can receive production workloads."


def _health_check_failure_status(error: BaseException) -> str:
    """Map one probe result to a control-plane status without overclaiming.

    Only explicit credential rejection and model-specific permanent errors may
    mark configuration unavailable. Rate limits have their own cooldown; all
    other probe failures are observations, not proof that a route is invalid.
    """
    if is_rate_limit_error(error):
        return "rate_limited"
    if "api key is not configured" in str(error or "").lower():
        return "unavailable"
    if is_provider_authentication_error(error) or is_model_unavailable_error(error):
        return "unavailable"
    return "probe_failed"


def _legacy_transient_probe_unavailable(message: Any) -> bool:
    """Recognize old probe failures that were incorrectly saved as unavailable."""
    normalized = str(message or "").lower()
    return any(
        marker in normalized
        for marker in (
            "empty assistant response",
            "empty completion response",
            "timed out",
            "timeout",
            "connection error",
            "connection reset",
            "returned html",
            "invalid structured json",
        )
    )


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


def _runtime_admission_settings(route: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve independent model request limits and provider telemetry."""
    model_params = route.get("extra_params")
    model_params = model_params if isinstance(model_params, dict) else {}

    auto_provider_maximum = _positive_int(
        route.get("runtime_provider_auto_max_concurrency"),
        _DEFAULT_PROVIDER_ADMISSION_MAX_CONCURRENCY,
        maximum=4096,
    )
    # Provider capacity is telemetry only. Admission and pressure handling are
    # owned by each model, so sibling models never share a request permit.
    provider_maximum = auto_provider_maximum
    provider_minimum = provider_maximum
    model_maximum = _positive_int(
        model_params.get("runtime_max_concurrency"),
        _DEFAULT_MODEL_ADMISSION_MAX_CONCURRENCY,
        maximum=_MODEL_CONCURRENCY_CEILING,
    )
    model_minimum = min(
        model_maximum,
        _positive_int(model_params.get("runtime_min_concurrency"), 1),
    )
    return {
        "provider_minimum": provider_minimum,
        "provider_maximum": provider_maximum,
        "provider_auto": False,
        "model_minimum": model_minimum,
        "model_maximum": model_maximum,
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
        settings: Dict[str, Any],
    ) -> None:
        self._controller = controller
        self._provider_key = provider_key
        self._model_key = model_key
        self._settings = settings
        self._released = False

    async def release(self, *, success: bool, error: BaseException | None = None) -> None:
        if self._released:
            return
        self._released = True
        await self._controller.release(
            self._provider_key,
            self._model_key,
            self._settings,
            success=success,
            error=error,
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
        self._provider_settings: Dict[str, Dict[str, Any]] = {}
        self._model_limits: Dict[str, int] = {}

    def _ensure_loop(self) -> asyncio.Condition:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._condition is None:
            self._loop = loop
            self._condition = asyncio.Condition()
            self._provider_inflight = {}
            self._model_inflight = {}
            self._provider_settings = {}
            self._model_limits = {}
        return self._condition

    def _remember_provider_settings(
        self,
        provider_key: str,
        settings: Dict[str, Any],
    ) -> None:
        self._provider_settings[provider_key] = dict(settings)

    def score(self, route: Dict[str, Any]) -> tuple[float, float]:
        """Return live pressure for fair candidate ordering without a DB read."""
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        self._remember_provider_settings(provider_key, settings)
        self._model_limits[model_key] = settings["model_maximum"]
        model_load = self._model_inflight.get(model_key, 0) / settings["model_maximum"]
        return model_load, model_load

    def snapshot(self, route: Dict[str, Any]) -> Dict[str, Any]:
        """Expose the active request budget for the Model Center control plane."""
        self._ensure_loop()
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        self._remember_provider_settings(provider_key, settings)
        self._model_limits[model_key] = settings["model_maximum"]
        provider_capacity = settings["provider_maximum"]
        return {
            "runtime_provider_capacity": provider_capacity,
            "runtime_provider_inflight": self._provider_inflight.get(provider_key, 0),
            "runtime_provider_min_capacity": settings["provider_minimum"],
            "runtime_provider_max_capacity": settings["provider_maximum"],
            "runtime_provider_auto_concurrency": settings["provider_auto"],
            "runtime_provider_success_streak": 0,
            "runtime_provider_backoff_remaining_seconds": 0,
            "runtime_model_capacity": settings["model_maximum"],
            "runtime_model_inflight": self._model_inflight.get(model_key, 0),
        }

    def provider_snapshots(self) -> Dict[str, Dict[str, Any]]:
        """Expose worker-safe provider telemetry for runtime heartbeats."""
        snapshots: Dict[str, Dict[str, Any]] = {}
        for provider_key, settings in list(self._provider_settings.items()):
            provider_capacity = settings["provider_maximum"]
            snapshots[provider_key] = {
                "runtime_provider_capacity": provider_capacity,
                "runtime_provider_inflight": self._provider_inflight.get(provider_key, 0),
                "runtime_provider_min_capacity": settings["provider_minimum"],
                "runtime_provider_max_capacity": settings["provider_maximum"],
                "runtime_provider_auto_concurrency": settings["provider_auto"],
                "runtime_provider_success_streak": 0,
                "runtime_provider_backoff_remaining_seconds": 0,
            }
        return snapshots

    def model_snapshots(self) -> Dict[str, Dict[str, int]]:
        """Expose live per-model request counts from the worker process."""
        return {
            model_key: {
                "runtime_model_capacity": limit,
                "runtime_model_inflight": self._model_inflight.get(model_key, 0),
            }
            for model_key, limit in list(self._model_limits.items())
        }

    async def acquire(self, route: Dict[str, Any]) -> RuntimeRouteAdmissionLease:
        """Wait for a permit without treating local backpressure as model failure.

        A permit is released whenever the in-flight provider call completes,
        times out, or is cancelled.  Waiting here is therefore the correct
        backpressure mechanism; imposing an unrelated queue deadline made a
        healthy but busy provider look like it had timed out upstream.
        """
        condition = self._ensure_loop()
        provider_key, model_key = _runtime_admission_keys(route)
        settings = _runtime_admission_settings(route)
        self._remember_provider_settings(provider_key, settings)
        self._model_limits[model_key] = settings["model_maximum"]

        async with condition:
            while True:
                provider_inflight = self._provider_inflight.get(provider_key, 0)
                model_inflight = self._model_inflight.get(model_key, 0)
                if model_inflight < settings["model_maximum"]:
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
        settings: Dict[str, Any],
        *,
        success: bool,
        error: BaseException | None = None,
    ) -> None:
        condition = self._ensure_loop()
        async with condition:
            self._provider_inflight[provider_key] = max(0, self._provider_inflight.get(provider_key, 1) - 1)
            self._model_inflight[model_key] = max(0, self._model_inflight.get(model_key, 1) - 1)
            condition.notify_all()


runtime_route_admission = RuntimeRouteAdmissionController()


def runtime_route_admission_score(route: Dict[str, Any]) -> tuple[float, float]:
    """Expose current request pressure for BaseAgent candidate ordering."""
    return runtime_route_admission.score(route)


def runtime_route_admission_snapshot(route: Dict[str, Any]) -> Dict[str, Any]:
    """Expose request admission budget for runtime API and dashboard views."""
    return runtime_route_admission.snapshot(route)


def runtime_provider_admission_snapshots() -> Dict[str, Dict[str, Any]]:
    """Expose aggregate provider telemetry to the worker heartbeat."""
    return runtime_route_admission.provider_snapshots()


def runtime_model_admission_snapshots() -> Dict[str, Dict[str, int]]:
    """Expose independent model request budgets to the worker heartbeat."""
    return runtime_route_admission.model_snapshots()


async def acquire_runtime_route_admission(route: Dict[str, Any]) -> RuntimeRouteAdmissionLease:
    """Acquire a model permit before issuing a live model request."""
    return await runtime_route_admission.acquire(route)


def get_provider_rate_limit_state(provider: AIProviderConfig) -> Dict[str, Any]:
    state = _rate_limit_state(provider.extra_config)
    return {
        # Keep old counters visible, but do not surface legacy provider
        # cooldowns as active because routing limits are model scoped.
        "rate_limit_active": False,
        "rate_limit_cooldown_until": None,
        "rate_limit_remaining_seconds": 0,
        "rate_limit_count": state["rate_limit_count"],
        "last_rate_limit_at": state["last_rate_limit_at_iso"],
    }


def get_provider_runtime_health_state(provider: AIProviderConfig) -> Dict[str, Any]:
    state = _runtime_health_state(provider.extra_config)
    return {
        **state,
        "runtime_failure_active": False,
        "runtime_failure_cooldown_until": None,
        "runtime_failure_remaining_seconds": 0,
    }


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


def is_provider_authentication_error(error: Any) -> bool:
    """Detect a provider credential rejection without treating model ACLs as auth failures.

    A 401 means the shared provider credential cannot authenticate any sibling
    model route. In contrast, a model-specific 403 (for example, no access to
    one catalogue entry) remains a model-unavailable condition so healthy
    siblings can still be used.
    """
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    if normalized_status == 401:
        return True
    if normalized_status == 403:
        # Preserve the existing model-level ACL fallback behaviour. A provider
        # that returns 403 for one restricted model may still serve siblings.
        return False

    code_candidates = _extract_error_codes(error)
    if any(
        code in {
            "invalid_token",
            "invalid_api_key",
            "invalid_api-key",
            "incorrect_api_key",
            "authentication_error",
            "authentication_failed",
            "invalid_credentials",
            "unauthorized",
        }
        for code in code_candidates
    ):
        return True

    message = str(error or "").lower()
    return any(
        marker in message
        for marker in (
            "invalid token",
            "invalid api key",
            "invalid api-key",
            "invalid_api_key",
            "api key is invalid",
            "incorrect api key",
            "invalid credentials",
            "authentication failed",
            "authentication error",
            "credential rejected",
        )
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
    if is_provider_authentication_error(error):
        return False

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
            "access denied",
            "permission denied",
            "invalid model",
            "unsupported model",
            "unknown model",
            "无权访问",
            "没有权限",
            "无访问权限",
        ]
    )

    # 404 + model-related message usually means route/model is invalid for this provider key.
    if normalized_status in {403, 404} and message_hit:
        return True

    return message_hit


def is_model_channel_failure(error: Any) -> bool:
    """Detect temporary runtime routing failures when provider channels are missing."""
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    code_candidates = _extract_error_codes(error)
    if any(code in _ROUTE_CHANNEL_FAILURE_CODES for code in code_candidates):
        return True

    # NewAI/compatible providers emit this as a 500 code with explicit channel
    # routing context in the message.
    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None

    if normalized_status == 500:
        message = str(error).lower()
        return "可用渠道不存在" in str(error) or "no available channel" in message

    return False


def _effective_route_check_status(model_status: Any, provider_status: Any) -> str:
    """Return the route status, with provider-wide outages taking precedence."""
    normalized_provider = str(provider_status or "").strip().lower()
    normalized_model = str(model_status or "").strip().lower()
    if normalized_provider == "unavailable":
        return "unavailable"
    return normalized_model or normalized_provider


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
            effective_status = _effective_route_check_status(model_status, provider_status)
            status_routable = effective_status != "unavailable"
            has_api_key = bool(provider.api_key)
            outside_window = not route_available_at(model.extra_params)
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
        provider_auto_maxima: Dict[int, int] = {}
        for route in routes:
            provider_id = int(route["provider_id"])
            model_params = route.get("extra_params")
            model_params = model_params if isinstance(model_params, dict) else {}
            model_capacity = _positive_int(
                model_params.get("runtime_max_concurrency"),
                _DEFAULT_MODEL_ADMISSION_MAX_CONCURRENCY,
                maximum=_MODEL_CONCURRENCY_CEILING,
            )
            provider_auto_maxima[provider_id] = (
                provider_auto_maxima.get(provider_id, 0) + model_capacity
            )

        for route in routes:
            route["runtime_provider_auto_max_concurrency"] = max(
                1,
                provider_auto_maxima.get(int(route["provider_id"]), 0),
            )
            route.update(runtime_route_admission_snapshot(route))

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
                continue
            if isinstance(item, dict):
                kind = str(item.get("type") or "").lower()
                if kind in {"thinking", "reasoning", "redacted_thinking"}:
                    continue
                text = item.get("text")
                if text is None:
                    text = item.get("content")
            else:
                kind = str(getattr(item, "type", "") or "").lower()
                if kind in {"thinking", "reasoning", "redacted_thinking"}:
                    continue
                text = getattr(item, "text", None)
                if text is None:
                    text = getattr(item, "content", None)
            if isinstance(text, str):
                parts.append(text)
            elif text is not None and not isinstance(text, (dict, list)):
                parts.append(str(text))
        return "\n".join(part for part in parts if part)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return str(content)


def visible_assistant_text(content: Any) -> str:
    """Return the answer text, without a reasoning trace.

    Gateways expose reasoning as ``reasoning_content``, as typed content blocks,
    or inline as ``<think>`` / ``<thinking>`` tags (the formats used by Kiro-Go
    and New API). The answer is the remaining text. Reasoning is not promoted
    into the answer.
    """
    text = _THINK_BLOCK_RE.sub("", _content_to_text(content))
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def _openai_response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return visible_assistant_text(getattr(message, "content", None))
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
                    return visible_assistant_text(message.get("content"))
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


THINKING_MODES = ("default", "enabled", "disabled")


def route_completion_extra_body(route: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the model's explicitly configured provider request options.

    Probes and production workloads share this one source so a route is
    judged under the same options it is served with. No provider is special
    cased: behaviour such as deep thinking is chosen per model in the console.
    """
    model_params = route.get("extra_params")
    configured = model_params.get("extra_body") if isinstance(model_params, dict) else None
    return dict(configured) if isinstance(configured, dict) and configured else None


def get_thinking_mode(extra_params: Any) -> str:
    """Read the console's thinking switch from a model's ``extra_params``.

    ``default`` means nothing is sent and the provider's own default applies.
    """
    body = extra_params.get("extra_body") if isinstance(extra_params, dict) else None
    thinking = body.get("thinking") if isinstance(body, dict) else None
    mode = thinking.get("type") if isinstance(thinking, dict) else None
    return mode if mode in ("enabled", "disabled") else "default"


def with_thinking_mode(extra_params: Any, mode: str) -> Dict[str, Any]:
    """Return a copy of ``extra_params`` with the thinking switch applied.

    Only ``extra_body.thinking`` is touched; sibling ``extra_body`` keys and
    unrelated parameters are preserved. Re-applying the current mode is a
    no-op, so provider-specific values the switch cannot express survive an
    unrelated save.
    """
    if mode not in THINKING_MODES:
        raise ValueError(f"Unsupported thinking mode: {mode!r}")
    params = dict(extra_params) if isinstance(extra_params, dict) else {}
    if mode == get_thinking_mode(params):
        return params

    existing = params.get("extra_body")
    body = dict(existing) if isinstance(existing, dict) else {}
    if mode == "default":
        body.pop("thinking", None)
    else:
        body["thinking"] = {"type": mode}
    if body:
        params["extra_body"] = body
    else:
        params.pop("extra_body", None)
    return params


def runtime_route_request_timeout_seconds(
    route: Dict[str, Any], default: Optional[float]
) -> Optional[float]:
    """Resolve the live API deadline independently for each model/provider."""
    model_params = route.get("extra_params")
    model_params = model_params if isinstance(model_params, dict) else {}
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    configured = model_params.get("runtime_request_timeout_seconds")
    if configured in (None, ""):
        configured = provider_config.get("runtime_request_timeout_seconds")
    if configured in (None, ""):
        return default
    try:
        return float(max(10, min(600, int(configured))))
    except (TypeError, ValueError):
        return default


def runtime_route_completion_max_tokens(route: Dict[str, Any], agent_limit: int | None = None) -> int | None:
    """Completion requests are not capped.

    A stored route limit or an agent budget used to be forwarded as
    ``max_tokens``. Reasoning models spend that budget before the answer, so
    every new model looked broken until someone raised the number. Callers keep
    this function so older call sites stay valid; it never returns a cap.
    """
    del route, agent_limit
    return None


def normalize_availability_timezone(value: Any) -> str:
    """Return an IANA timezone name, or raise ``ValueError``."""
    name = str(value or "").strip() or _DEFAULT_AVAILABILITY_TIMEZONE
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc
    return name


def _clock_minutes(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def normalize_availability_windows(windows: Any) -> List[Dict[str, str]]:
    """Validate ``[{start, end}]`` clock windows.

    An empty list means the model is always available. A window whose end is
    earlier than its start crosses midnight, which is how a night-discount
    period such as 22:00–08:00 is written. A window of equal endpoints is
    rejected so a typo cannot look like a full-day schedule.
    """
    if windows in (None, ""):
        return []
    if not isinstance(windows, Sequence) or isinstance(windows, (str, bytes)):
        raise ValueError("Availability windows must be a list of start and end times")
    if len(windows) > 8:
        raise ValueError("A model can have at most 8 availability windows")
    normalized: List[Dict[str, str]] = []
    for item in windows:
        if isinstance(item, dict):
            start, end = item.get("start"), item.get("end")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            start, end = item
        else:
            raise ValueError("Each availability window needs a start and an end")
        start_minutes = _clock_minutes(start)
        end_minutes = _clock_minutes(end)
        if start_minutes is None or end_minutes is None:
            raise ValueError("Availability times must use HH:MM in 24-hour form")
        if start_minutes == end_minutes:
            raise ValueError("An availability window cannot start and end at the same time")
        normalized.append({"start": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}", "end": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"})
    return normalized


def with_availability(extra_params: Any, timezone_name: Any, windows: Any) -> Dict[str, Any]:
    """Return ``extra_params`` with the availability schedule applied.

    An empty window list removes the schedule, which means the model is always
    available. Other extra parameters are preserved.
    """
    params = dict(extra_params) if isinstance(extra_params, dict) else {}
    normalized = normalize_availability_windows(windows)
    if not normalized:
        params.pop("availability_windows", None)
        params.pop("availability_timezone", None)
        return params
    params["availability_timezone"] = normalize_availability_timezone(
        timezone_name or params.get("availability_timezone") or _DEFAULT_AVAILABILITY_TIMEZONE
    )
    params["availability_windows"] = normalized
    return params


def availability_view(extra_params: Any) -> Dict[str, Any]:
    """Read the stored schedule for the control panel."""
    params = extra_params if isinstance(extra_params, dict) else {}
    raw_windows = params.get("availability_windows")
    windows: List[Dict[str, str]] = []
    if isinstance(raw_windows, list):
        for item in raw_windows:
            if not isinstance(item, dict):
                continue
            start, end = _clock_minutes(item.get("start")), _clock_minutes(item.get("end"))
            if start is None or end is None or start == end:
                continue
            windows.append({"start": f"{start // 60:02d}:{start % 60:02d}", "end": f"{end // 60:02d}:{end % 60:02d}"})
    timezone_name = str(params.get("availability_timezone") or _DEFAULT_AVAILABILITY_TIMEZONE)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = _DEFAULT_AVAILABILITY_TIMEZONE
    return {"availability_timezone": timezone_name, "availability_windows": windows}


def route_available_at(extra_params: Any, moment: Optional[datetime] = None) -> bool:
    """Whether a model may be called at ``moment``.

    No usable windows means always available. Invalid stored values fail open
    so a bad schedule cannot take every route offline. Comparison uses the
    schedule timezone and ignores seconds.
    """
    view = availability_view(extra_params)
    windows = view["availability_windows"]
    if not windows:
        return True
    when = moment or _utcnow()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local = when.astimezone(ZoneInfo(view["availability_timezone"]))
    minute = local.hour * 60 + local.minute
    for item in windows:
        start = _clock_minutes(item["start"])
        end = _clock_minutes(item["end"])
        if start is None or end is None:
            continue
        if start < end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:
            return True
    return False


def drop_rejected_optional_parameter(
    error: Any,
    payload: Dict[str, Any],
    already_dropped: set[str],
) -> bool:
    """Drop one optional field a gateway refused, and report whether to retry.

    The field name comes from the error. ``model`` and ``messages`` are never
    dropped. A refusal of ``thinking`` removes only that key from ``extra_body``.
    This is the same idea as a gateway adaptor that passes unknown models
    through and rewrites a request only after the upstream names the problem.
    """
    message = str(error or "")
    lowered = message.lower()
    if not any(marker in lowered for marker in _OPTIONAL_REJECTION_MARKERS):
        return False
    names: List[str] = []
    names.extend(re.findall(r"['\"]([A-Za-z][A-Za-z0-9_]{0,40})['\"]", message))
    labeled = re.search(r"(?:parameter|参数)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_]{0,40})", message, re.IGNORECASE)
    if labeled:
        names.append(labeled.group(1))
    for name in names:
        if name in already_dropped or name in _PROTECTED_COMPLETION_FIELDS:
            continue
        if name == "thinking":
            body = payload.get("extra_body")
            if isinstance(body, dict) and "thinking" in body:
                kept = {key: value for key, value in body.items() if key != "thinking"}
                if kept:
                    payload["extra_body"] = kept
                else:
                    payload.pop("extra_body", None)
                already_dropped.add(name)
                return True
        if name in payload:
            payload.pop(name, None)
            already_dropped.add(name)
            return True
    return False


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


def _openai_base_url_candidates(base_url: str) -> List[Optional[str]]:
    """Try /v1 only when the configured URL has no versioned API path."""
    base_url = base_url.rstrip("/")
    candidates: List[Optional[str]] = [base_url or None]
    last_segment = urlsplit(base_url).path.rstrip("/").rsplit("/", 1)[-1]
    if base_url and not re.fullmatch(r"v\d+(?:[._-].+)?", last_segment, re.IGNORECASE):
        candidates.append(f"{base_url}/v1")
    return candidates


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

    candidates = _openai_base_url_candidates(base_url)
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
            if getattr(exc, "status_code", None) != 404:
                break
    return {
        "success": False,
        "status": _health_check_failure_status(last_error or RuntimeError("Model catalogue request failed")),
        "message": str(last_error or "Model catalogue request failed"),
        "models": [],
    }


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

    # Literature enrichment accepts JSON wrapped in a Markdown fence (and a
    # small amount of surrounding prose), which is common for otherwise valid
    # model responses. Keep the health probe aligned with that production
    # contract so formatting alone cannot strand a capable route as degraded.
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1)
    else:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Structured workload probe did not return valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "globalid-structured-probe-ok":
        raise RuntimeError("Structured workload probe returned an unexpected JSON payload")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 3 or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("Structured workload probe must return exactly three evidence items")
    if [item.get("id") for item in items] != [1, 2, 3]:
        raise RuntimeError("Structured workload probe returned unexpected evidence item ids")
    for item in items:
        if not str(item.get("title") or "").strip():
            raise RuntimeError("Structured workload probe omitted an evidence title")
        if len(str(item.get("summary") or "").strip()) < 60:
            raise RuntimeError("Structured workload probe returned an incomplete evidence summary")
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Structured workload probe returned an invalid confidence value") from exc
        if not 0 <= confidence <= 1:
            raise RuntimeError("Structured workload probe returned confidence outside the 0..1 range")


def _test_timeout_seconds(route: Dict[str, Any]) -> float:
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    model_params = route.get("extra_params")
    model_params = model_params if isinstance(model_params, dict) else {}
    raw = model_params.get("health_check_timeout_seconds")
    if raw in (None, ""):
        raw = provider_config.get("health_check_timeout_seconds", 180)
    try:
        return float(max(10, min(600, int(raw))))
    except (TypeError, ValueError):
        return 180.0


async def _test_openai_compatible(
    route: Dict[str, Any], model_name: str, *, structured: bool = False
) -> Dict[str, Any]:
    from src.ai.work_queue import require_dispatcher
    require_dispatcher()
    base_urls = _openai_base_url_candidates(str(route.get("base_url") or ""))

    last_error: Optional[Exception] = None
    for candidate_base_url in base_urls:
        client = AsyncOpenAI(
            api_key=route.get("api_key"),
            base_url=candidate_base_url,
            default_headers=(route.get("extra_headers") or None),
            timeout=_test_timeout_seconds(route),
            max_retries=0,
        )
        prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You are validating an evidence-summarization model used by a public-health literature pipeline.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        extra_body = route_completion_extra_body(route)
        if extra_body:
            payload["extra_body"] = extra_body
        dropped_parameters: set[str] = set()
        try:
            while True:
                try:
                    response = await client.chat.completions.create(**payload)
                    break
                except Exception as exc:
                    if len(dropped_parameters) >= 4 or not drop_rejected_optional_parameter(exc, payload, dropped_parameters):
                        raise
                    logger.warning(
                        "Probe dropped unsupported parameter for model '{}': {}",
                        model_name,
                        ", ".join(sorted(dropped_parameters)),
                    )
        except Exception as exc:
            last_error = exc
            # An alternate API path can fix a missing endpoint, but cannot fix
            # a completed response, rate limit, timeout, or provider failure.
            if getattr(exc, "status_code", None) != 404:
                raise
            continue

        text = _openai_response_text(response)
        if not text.strip():
            first = response.choices[0] if getattr(response, "choices", None) else None
            finish_reason = getattr(first, "finish_reason", None)
            completion_tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)
            raise RuntimeError(
                "Chat completion produced no final assistant content "
                f"(finish_reason={finish_reason!r}, completion_tokens={completion_tokens!r})"
            )
        if structured:
            _validate_structured_test_response(text)
        else:
            _validate_test_response(text)
        return {
            "base_url": candidate_base_url or "default-openai-base-url",
            "response_preview": _response_preview(text),
        }

    if last_error is not None:
        raise last_error
    raise RuntimeError("Chat completion returned no usable response")


async def _test_anthropic(
    route: Dict[str, Any], model_name: str, *, structured: bool = False
) -> Dict[str, Any]:
    from src.ai.work_queue import require_dispatcher
    require_dispatcher()
    prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
    client = AsyncAnthropic(
        api_key=route.get("api_key"),
        timeout=_test_timeout_seconds(route),
        max_retries=0,
    )
    response = await client.messages.create(
        model=model_name,
        system="You are validating an evidence-summarization model used by a public-health literature pipeline.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=ANTHROPIC_PROTOCOL_MAX_TOKENS,
        temperature=0,
    )
    text = visible_assistant_text(
        [
            block
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
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
    from src.ai.work_queue import require_dispatcher
    require_dispatcher()
    style = str(route.get("api_style") or "openai_compatible").lower()
    model_name = str(route.get("model_name") or "")

    try:
        if not route.get("api_key"):
            raise RuntimeError("API key is not configured")

        if style == "anthropic":
            details = await _test_anthropic(route, model_name, structured=structured)
        else:
            details = await _test_openai_compatible(route, model_name, structured=structured)

        probe_name = "Literature workload probe" if structured else "Chat completion test"
        marker = "globalid-structured-probe-ok" if structured else _MODEL_TEST_MARKER
        message = f"{probe_name} successful. Response: {details.get('response_preview') or marker}"

        return {
            "success": True,
            "status": "available",
            "message": message,
            "model_name": model_name,
            "test_type": "literature_workload" if structured else "chat_completion",
            "test_prompt": _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT,
            **details,
        }
    except Exception as exc:
        failure_scope = (
            "provider"
            if is_provider_authentication_error(exc)
            else "model"
            if is_model_unavailable_error(exc)
            else None
        )
        return {
            "success": False,
            "status": _health_check_failure_status(exc),
            "failure_scope": failure_scope,
            "message": str(exc),
            "model_name": model_name,
            "test_type": "literature_workload" if structured else "chat_completion",
            "test_prompt": _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT,
            "retry_after_seconds": extract_retry_after_seconds(exc),
        }


async def update_model_check_result(model_id: int, status: str, message: str) -> None:
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id)
        if model is None:
            return
        # A failed probe is not a configuration verdict. Preserve any prior
        # hard-unavailable finding until a success proves recovery or an
        # explicit permanent failure is observed again.
        if (
            status in {"available", "unavailable", "invalid"}
            or str(model.last_check_status or "").lower() != "unavailable"
            or _legacy_transient_probe_unavailable(model.last_check_message)
        ):
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
        # Provider responses may include an echoed token or authorization
        # header. Keep the control-plane status actionable without persisting
        # any upstream credential detail.
        safe_message = (
            _PROVIDER_AUTHENTICATION_FAILURE_MESSAGE
            if is_provider_authentication_error(message)
            else message
        )
        if (
            status in {"available", "unavailable", "invalid"}
            or str(provider.last_check_status or "").lower() != "unavailable"
            or _legacy_transient_probe_unavailable(provider.last_check_message)
        ):
            provider.last_check_status = status
            provider.last_check_message = safe_message
        provider.last_checked_at = _utcnow()
        if status == "available":
            provider.extra_config = _write_runtime_success(
                _clear_payload_rate_limit(provider.extra_config, provider.last_checked_at),
                occurred_at=provider.last_checked_at,
                duration_seconds=None,
            )
        await db.commit()


def _provider_check_result_from_model_results(results: List[Dict[str, Any]]) -> tuple[str, str]:
    """Summarize sibling model checks without letting one bad model poison the provider."""
    successful = next((item for item in results if item.get("success")), None)
    if successful is not None:
        return "available", str(successful.get("message") or "At least one enabled provider model is available")

    rate_limited = next((item for item in results if item.get("status") == "rate_limited"), None)
    if rate_limited is not None:
        return "probe_failed", str(rate_limited.get("message") or "One or more model routes are rate-limited")

    if results and all(item.get("status") == "unavailable" for item in results):
        failures = [
            f"{item.get('model_name') or item.get('model_key') or 'model'}: {item.get('message') or 'unavailable'}"
            for item in results
        ]
        return "unavailable", "All enabled provider models are unavailable: " + " | ".join(failures)

    probe_failure = next((item for item in results if item.get("status") == "probe_failed"), None)
    if probe_failure is not None:
        return "probe_failed", str(probe_failure.get("message") or "Provider health probe failed transiently")

    failures = [
        f"{item.get('model_name') or item.get('model_key') or 'model'}: {item.get('message') or 'unavailable'}"
        for item in results
    ]
    return "unavailable", "All enabled provider models failed: " + " | ".join(failures)


async def mark_route_rate_limited(
    route: Dict[str, Any],
    message: str,
    retry_after_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist a model-only cooldown after a runtime rate-limit error."""
    route_extra_params = route.get("extra_params") or {}
    cooldown_seconds = retry_after_seconds
    if cooldown_seconds is None:
        cooldown_seconds = int(
            route_extra_params.get("rate_limit_cooldown_seconds")
            or get_config().ai.rate_limit_cooldown_seconds
        )

    now = _utcnow()
    cooldown_until = now + timedelta(seconds=max(1, int(cooldown_seconds)))

    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))

        if model is not None:
            if str(model.last_check_status or "").lower() != "unavailable":
                model.last_check_status = "rate_limited"
                model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _mark_payload_rate_limited(model.extra_params, cooldown_until, now)

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
    """Persist a real production-call transport failure for this model route.

    Rate-limit and invalid-model failures already have durable specialised
    handlers. This function owns timeout, connection and transient 5xx
    failures at model scope; sibling routes must remain independently usable.
    """
    kind = _runtime_failure_kind(error)
    if kind not in _RUNTIME_FAILURE_KINDS:
        return {"recorded": False, "reason": "not_a_transport_failure"}

    try:
        model_id = int(route.get("model_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = _utcnow()
    message = str(error or "")[:1000]
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
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

        await db.commit()

    return {
        "recorded": True,
        "kind": kind,
        "cooldown_seconds": model_state["runtime_failure_remaining_seconds"],
        "provider_circuit_open": False,
    }


async def record_route_runtime_success(
    route: Dict[str, Any],
    *,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist a successful production completion and its observed latency."""
    try:
        model_id = int(route.get("model_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_persistent_ids"}

    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, model_id, with_for_update=True)
        if model is None:
            return {"recorded": False, "reason": "model_not_found"}

        model.extra_params = _write_runtime_success(
            model.extra_params,
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


async def mark_provider_authentication_failed(
    route: Dict[str, Any],
    error: BaseException | str | None = None,
) -> Dict[str, Any]:
    """Durably block every route sharing rejected provider credentials.

    The database intentionally stores a generic remediation message instead
    of the upstream error because provider errors can echo credential material.
    Provider activation is left unchanged so an operator can update the
    credential and explicitly recover the provider through its connection test.
    """
    try:
        provider_id = int(route.get("provider_id"))
    except (TypeError, ValueError):
        return {"recorded": False, "reason": "route_has_no_provider_id"}

    now = _utcnow()
    async with get_db() as db:
        provider = await db.get(AIProviderConfig, provider_id)
        if provider is None:
            return {"recorded": False, "reason": "provider_not_found"}

        provider.last_check_status = "unavailable"
        provider.last_check_message = _PROVIDER_AUTHENTICATION_FAILURE_MESSAGE
        provider.last_checked_at = now
        provider.extra_config = _clear_payload_rate_limit(provider.extra_config, now)
        await db.commit()

    # Keep the parameter for an expressive call site while ensuring raw
    # upstream errors never enter durable provider status fields.
    del error
    return {
        "recorded": True,
        "provider_id": provider_id,
        "status": "unavailable",
    }


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
    """Clear the model route cooldown after that route has recovered."""
    now = _utcnow()
    async with get_db() as db:
        model = await db.get(AIModelConfig, int(route["model_id"]))

        if model is not None:
            model.last_check_status = "available"
            model.last_check_message = message
            model.last_checked_at = now
            model.extra_params = _clear_payload_rate_limit(model.extra_params, now)

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

    check_started_at = time.perf_counter()
    result = await test_route_connection(route, structured=structured)
    check_error = None if result.get("success") else RuntimeError(
        str(result.get("message") or "Model-center health check failed")
    )
    await record_model_call_log(
        request_id=uuid4(),
        route=route,
        model_name=str(route["model_name"]),
        provider_key=str(route["provider_key"]),
        agent_name="model_center_health_check",
        status="success" if result.get("success") else "failed",
        latency_ms=(time.perf_counter() - check_started_at) * 1000,
        error=check_error,
    )
    if result["status"] == "rate_limited":
        await mark_route_rate_limited(
            route,
            result["message"],
            retry_after_seconds=result.get("retry_after_seconds"),
        )
    await update_model_check_result(model_id, result["status"], result["message"])
    if result.get("success"):
        await update_provider_check_result(route["provider_id"], result["status"], result["message"])
    elif result["status"] == "unavailable" and (
        result.get("failure_scope") == "provider"
        or is_provider_authentication_error(result["message"])
    ):
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

    model_results: List[Dict[str, Any]] = []
    for model in models:
        result = await check_model_by_id(model.id, structured=structured)
        if result.get("success"):
            return result
        result["model_name"] = model.model_name
        model_results.append(result)
    status, message = _provider_check_result_from_model_results(model_results)
    result = {"success": False, "status": status, "message": message, "models": model_results}
    await update_provider_check_result(provider_id, result["status"], result["message"])
    return result


def _health_check_concurrency(route: Dict[str, Any]) -> int:
    """Use a small provider-specific batch to catch concurrency-only failures."""
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    configured = provider_config.get("health_check_concurrency")
    try:
        provider_maximum = max(1, int(route.get("runtime_provider_max_capacity") or 1))
    except (TypeError, ValueError):
        provider_maximum = 1
    return _positive_int(configured, min(2, provider_maximum), maximum=provider_maximum)


async def check_all_models(*, structured: bool = True) -> List[Dict[str, Any]]:
    """Test all routes with realistic, provider-bounded parallel workload probes."""
    routes = await get_runtime_routes()
    semaphores: Dict[int, asyncio.Semaphore] = {}
    for route in routes:
        provider_id = int(route["provider_id"])
        semaphores.setdefault(
            provider_id,
            asyncio.Semaphore(_health_check_concurrency(route)),
        )

    async def _check(route: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = int(route["provider_id"])
        if route.get("routing_status") == "outside_window":
            return {
                "success": True,
                "status": "skipped",
                "message": "Skipped: outside the model's availability window",
                "model_id": route["model_id"],
                "provider_id": provider_id,
                "provider_key": route["provider_key"],
                "model_name": route.get("model_name"),
            }
        async with semaphores[provider_id]:
            result = await test_route_connection(route, structured=structured)
            if result["status"] == "rate_limited":
                await mark_route_rate_limited(
                    route,
                    result["message"],
                    retry_after_seconds=result.get("retry_after_seconds"),
                )
            await update_model_check_result(route["model_id"], result["status"], result["message"])
            result.update(
                {
                    "model_id": route["model_id"],
                    "provider_id": provider_id,
                    "provider_key": route["provider_key"],
                    "provider_probe_concurrency": _health_check_concurrency(route),
                }
            )
            return result

    results = list(await asyncio.gather(*(_check(route) for route in routes)))
    provider_results: Dict[int, List[Dict[str, Any]]] = {}
    for result in results:
        provider_results.setdefault(int(result["provider_id"]), []).append(result)
    for provider_id, provider_model_results in provider_results.items():
        checked = [item for item in provider_model_results if item.get("status") != "skipped"]
        if not checked:
            continue
        status, message = _provider_check_result_from_model_results(checked)
        await update_provider_check_result(provider_id, status, message)
    return results


async def probe_degraded_model_routes(
    *,
    limit: int = 2,
    minimum_interval_seconds: int = _MODEL_RECOVERY_PROBE_INTERVAL_SECONDS,
) -> List[Dict[str, Any]]:
    """Run bounded realistic probes so chronically failed routes can recover.

    Production admission excludes a chronic route, so it cannot generate the
    success that clears its own circuit. The worker calls this function in a
    separate low-rate sweep after ordinary cooldowns have expired.
    """
    routes = await get_runtime_routes()
    now = time.monotonic()
    due: List[Dict[str, Any]] = []
    for route in routes:
        if not route.get("runtime_degraded"):
            continue
        if route.get("routing_status") == "outside_window":
            continue
        if route.get("runtime_failure_active") or route.get("rate_limit_active"):
            continue
        if not route.get("has_api_key"):
            continue
        try:
            model_id = int(route["model_id"])
        except (KeyError, TypeError, ValueError):
            continue
        last_attempt = _recovery_probe_attempted_at.get(model_id, float("-inf"))
        if now - last_attempt < max(30, int(minimum_interval_seconds)):
            continue
        due.append(route)

    due.sort(
        key=lambda route: (
            str(route.get("runtime_last_failure_at") or ""),
            int(route.get("priority") or 100),
            int(route.get("model_id") or 0),
        )
    )
    results: List[Dict[str, Any]] = []
    for route in due[: max(0, int(limit))]:
        model_id = int(route["model_id"])
        _recovery_probe_attempted_at[model_id] = time.monotonic()
        result = await check_model_by_id(model_id, structured=True)
        results.append(
            {
                "model_id": model_id,
                "model_key": route.get("model_key"),
                "success": bool(result.get("success")),
                "status": result.get("status"),
                "message": result.get("message"),
            }
        )
    return results
