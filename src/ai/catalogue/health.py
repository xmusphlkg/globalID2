"""Pure transitions and projections of persisted model routing health.

No database sessions, SDK clients or in-flight counters belong here. Writers
apply these transitions under their row lock; readers may project detached
catalogue records without retaining a connection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


class ModelHealthRecord(Protocol):
    extra_params: dict[str, Any]


class ProviderHealthRecord(Protocol):
    extra_config: dict[str, Any]


_ROUTING_STATE_KEY = "routing_state"

_RUNTIME_FAILURE_KINDS = {"timeout", "connection", "upstream"}

_RUNTIME_FAILURE_COOLDOWN_CAP_SECONDS = 120

_MODEL_TRANSPORT_CIRCUIT_THRESHOLD = 3

_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD = 8

_MODEL_FAILURE_STREAK_DECAY_INTERVAL = timedelta(minutes=30)

_RATE_LIMIT_BASE_COOLDOWN_SECONDS = 30

_RATE_LIMIT_STREAK_WINDOW_SECONDS = 600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
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


def _extract_routing_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    state = payload.get(_ROUTING_STATE_KEY)
    return dict(state) if isinstance(state, dict) else {}


def _write_routing_state(payload: Any, state: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload or {}) if isinstance(payload, dict) else {}
    if state:
        updated[_ROUTING_STATE_KEY] = state
    else:
        updated.pop(_ROUTING_STATE_KEY, None)
    return updated


def _rate_limit_state(payload: Any, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utcnow()
    state = _extract_routing_state(payload)
    cooldown_until = _parse_datetime(state.get("cooldown_until"))
    last_rate_limit_at = _parse_datetime(state.get("last_rate_limit_at"))
    active = bool(cooldown_until and cooldown_until > now)
    remaining_seconds = (
        max(0, int((cooldown_until - now).total_seconds()))
        if active and cooldown_until
        else 0
    )
    try:
        count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        count = 0

    return {
        "cooldown_until": cooldown_until,
        "cooldown_until_iso": cooldown_until.isoformat() if cooldown_until else None,
        "last_rate_limit_at": last_rate_limit_at,
        "last_rate_limit_at_iso": last_rate_limit_at.isoformat()
        if last_rate_limit_at
        else None,
        "rate_limit_count": count,
        "rate_limit_active": active,
        "rate_limit_remaining_seconds": remaining_seconds,
    }


def _rate_limit_streak(payload: Any, now: datetime) -> int:
    """Consecutive rate limits since the last success, within the streak window."""
    state = _extract_routing_state(payload)
    last = _parse_datetime(state.get("last_rate_limit_at"))
    if last is None or (now - last).total_seconds() > _RATE_LIMIT_STREAK_WINDOW_SECONDS:
        return 0
    try:
        return max(0, int(state.get("rate_limit_streak") or 0))
    except (TypeError, ValueError):
        return 0


def _escalating_rate_limit_cooldown(streak: int, ceiling: int) -> int:
    """30s for an isolated limit, doubling per consecutive limit up to ``ceiling``.

    Pooled upstream accounts fail one request at a time. A full ceiling on the
    first failure parked whole routes while their siblings still had quota.
    """
    ceiling = max(1, int(ceiling))
    return min(
        ceiling, _RATE_LIMIT_BASE_COOLDOWN_SECONDS * 2 ** min(max(0, streak), 10)
    )


def _mark_payload_rate_limited(
    payload: Any, cooldown_until: datetime, occurred_at: datetime
) -> dict[str, Any]:
    streak = _rate_limit_streak(payload, occurred_at)
    state = _extract_routing_state(payload)
    try:
        current_count = int(state.get("rate_limit_count") or 0)
    except (TypeError, ValueError):
        current_count = 0
    state["cooldown_until"] = cooldown_until.isoformat()
    state["last_rate_limit_at"] = occurred_at.isoformat()
    state["rate_limit_count"] = current_count + 1
    state["rate_limit_streak"] = streak + 1
    return _write_routing_state(payload, state)


def _clear_payload_rate_limit(
    payload: Any, recovered_at: datetime | None = None
) -> dict[str, Any]:
    state = _extract_routing_state(payload)
    if not state:
        return dict(payload or {}) if isinstance(payload, dict) else {}

    state.pop("cooldown_until", None)
    state.pop("rate_limit_streak", None)
    if recovered_at is not None:
        state["last_recovered_at"] = recovered_at.isoformat()
    return _write_routing_state(payload, state)


def _runtime_health_state(payload: Any, now: datetime | None = None) -> dict[str, Any]:
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

    def _number(name: str) -> float | None:
        try:
            value = float(state.get(name))
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    raw_failure_streak = _integer("runtime_failure_streak")
    failure_streak = raw_failure_streak if failure_kind in _RUNTIME_FAILURE_KINDS else 0
    if last_failure_at and now > last_failure_at:
        decay_seconds = max(
            1, int(_MODEL_FAILURE_STREAK_DECAY_INTERVAL.total_seconds())
        )
        elapsed_intervals = int(
            (now - last_failure_at).total_seconds() // decay_seconds
        )
        failure_streak = max(0, failure_streak - elapsed_intervals)

    return {
        "runtime_failure_active": active,
        "runtime_failure_cooldown_until": cooldown_until.isoformat()
        if active and cooldown_until
        else None,
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
        "runtime_last_success_at": _parse_datetime(
            state.get("last_runtime_success_at")
        ),
        "runtime_last_error": str(state.get("last_runtime_error") or "").strip()
        or None,
    }


def _runtime_failure_cooldown_seconds(*, base_seconds: int, failure_streak: int) -> int:
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
    duration_seconds: float | None,
    cooldown_seconds: int,
) -> dict[str, Any]:
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
    duration_seconds: float | None,
) -> dict[str, Any]:
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
        state["runtime_latency_ewma_ms"] = round(
            (previous * 0.75) + (latency_ms * 0.25), 1
        )
    return _write_routing_state(payload, state)


def _combined_route_rate_limit_state(
    model: ModelHealthRecord,
    provider: ProviderHealthRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utcnow()
    model_state = _rate_limit_state(model.extra_params, now)
    provider_state = _rate_limit_state(provider.extra_config, now)
    last_model_rate_limit_at = (
        model_state["last_rate_limit_at_iso"]
        if model_state["rate_limit_count"] > 0
        else None
    )
    last_provider_rate_limit_at = (
        provider_state["last_rate_limit_at_iso"]
        if provider_state["rate_limit_count"] > 0
        else None
    )

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
    model: ModelHealthRecord,
    provider: ProviderHealthRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply production-call runtime health to this model route only.

    A timeout or connection failure may be model-specific even when sibling
    models share a provider. Provider-wide runtime circuits made one failing
    route suppress otherwise healthy sibling routes, so legacy provider
    runtime state is retained for diagnostics but ignored for admission.
    """
    now = now or _utcnow()
    model_state = _runtime_health_state(model.extra_params, now)
    active = model_state if model_state["runtime_failure_active"] else None
    scope: str | None = "model" if active else None

    latency = model_state["runtime_latency_ewma_ms"]
    failure_streak = model_state["runtime_failure_streak"]
    failure_streak_raw = model_state["runtime_failure_streak_raw"]
    degraded_scope: str | None = None
    degraded_reason: str | None = None
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
            active["runtime_failure_kind"]
            if active
            else model_state["runtime_failure_kind"]
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
            active["runtime_last_error"]
            if active
            else model_state["runtime_last_error"]
        ),
    }


def _route_routing_status(
    route: dict[str, Any],
    *,
    status_routable: bool,
    rate_limit_state: dict[str, Any],
    runtime_health_state: dict[str, Any],
) -> tuple[str, str]:
    """Describe actual route admission separately from the latest probe result."""
    if not route.get("has_api_key"):
        return "missing_credentials", "Provider API credentials are not configured."
    if rate_limit_state["rate_limit_active"]:
        return (
            "rate_limited",
            "The route is waiting for its rate-limit cooldown to expire.",
        )
    if runtime_health_state["runtime_failure_active"]:
        return (
            "cooling_down",
            "The route is temporarily cooling down after a production workload failure.",
        )
    if runtime_health_state["runtime_degraded"]:
        return (
            "degraded",
            "The route is excluded after repeated production failures and is awaiting an automatic recovery probe.",
        )
    if not status_routable:
        return (
            "probe_unavailable",
            "The latest model or provider workload probe did not succeed.",
        )
    return (
        "routable",
        "The route passed admission checks and can receive production workloads.",
    )


def _effective_route_check_status(model_status: Any, provider_status: Any) -> str:
    """Return the route status, with provider-wide outages taking precedence."""
    normalized_provider = str(provider_status or "").strip().lower()
    normalized_model = str(model_status or "").strip().lower()
    if normalized_provider == "unavailable":
        return "unavailable"
    return normalized_model or normalized_provider


def get_provider_rate_limit_state(provider: ProviderHealthRecord) -> dict[str, Any]:
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


def get_provider_runtime_health_state(provider: ProviderHealthRecord) -> dict[str, Any]:
    state = _runtime_health_state(provider.extra_config)
    return {
        **state,
        "runtime_failure_active": False,
        "runtime_failure_cooldown_until": None,
        "runtime_failure_remaining_seconds": 0,
    }


def get_model_rate_limit_state(
    model: ModelHealthRecord, provider: ProviderHealthRecord | None = None
) -> dict[str, Any]:
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
    model: ModelHealthRecord,
    provider: ProviderHealthRecord | None = None,
) -> dict[str, Any]:
    if provider is not None:
        return _combined_route_runtime_health_state(model, provider)
    return _runtime_health_state(model.extra_params)


def _provider_check_result_from_model_results(
    results: list[dict[str, Any]],
) -> tuple[str, str]:
    """Summarize sibling model checks without letting one bad model poison the provider."""
    successful = next((item for item in results if item.get("success")), None)
    if successful is not None:
        return "available", str(
            successful.get("message")
            or "At least one enabled provider model is available"
        )

    rate_limited = next(
        (item for item in results if item.get("status") == "rate_limited"), None
    )
    if rate_limited is not None:
        return "probe_failed", str(
            rate_limited.get("message") or "One or more model routes are rate-limited"
        )

    if results and all(item.get("status") == "unavailable" for item in results):
        failures = [
            f"{item.get('model_name') or item.get('model_key') or 'model'}: {item.get('message') or 'unavailable'}"
            for item in results
        ]
        return (
            "unavailable",
            "All enabled provider models are unavailable: " + " | ".join(failures),
        )

    probe_failure = next(
        (item for item in results if item.get("status") == "probe_failed"), None
    )
    if probe_failure is not None:
        return "probe_failed", str(
            probe_failure.get("message") or "Provider health probe failed transiently"
        )

    failures = [
        f"{item.get('model_name') or item.get('model_key') or 'model'}: {item.get('message') or 'unavailable'}"
        for item in results
    ]
    return "unavailable", "All enabled provider models failed: " + " | ".join(failures)
