"""Validated model options shared by probes and production requests.

All functions are deterministic apart from the optional current-time default
for availability. They copy persisted options instead of mutating caller data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ANTHROPIC_PROTOCOL_MAX_TOKENS = 64000

ANTHROPIC_RUNTIME_TIMEOUT_SECONDS = 600.0

_DEFAULT_AVAILABILITY_TIMEZONE = "Asia/Shanghai"

_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")

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

THINKING_MODES = ("default", "enabled", "disabled")


def route_completion_extra_body(route: dict[str, Any]) -> dict[str, Any] | None:
    """Return the model's explicitly configured provider request options.

    Probes and production workloads share this one source so a route is
    judged under the same options it is served with. No provider is special
    cased: behaviour such as deep thinking is chosen per model in the console.
    """
    model_params = route.get("extra_params")
    configured = (
        model_params.get("extra_body") if isinstance(model_params, dict) else None
    )
    return dict(configured) if isinstance(configured, dict) and configured else None


def get_thinking_mode(extra_params: Any) -> str:
    """Read the console's thinking switch from a model's ``extra_params``.

    ``default`` means nothing is sent and the provider's own default applies.
    """
    body = extra_params.get("extra_body") if isinstance(extra_params, dict) else None
    thinking = body.get("thinking") if isinstance(body, dict) else None
    mode = thinking.get("type") if isinstance(thinking, dict) else None
    return mode if mode in ("enabled", "disabled") else "default"


def with_thinking_mode(extra_params: Any, mode: str) -> dict[str, Any]:
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
    route: dict[str, Any], default: float | None
) -> float | None:
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


def normalize_availability_timezone(value: Any) -> str:
    """Return an IANA timezone name, or raise ``ValueError``."""
    name = str(value or "").strip() or _DEFAULT_AVAILABILITY_TIMEZONE
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc
    return name


def _clock_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _CLOCK_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def normalize_availability_windows(windows: Any) -> list[dict[str, str]]:
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
    normalized: list[dict[str, str]] = []
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
            raise ValueError(
                "An availability window cannot start and end at the same time"
            )
        normalized.append(
            {
                "start": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
                "end": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
            }
        )
    return normalized


def with_availability(
    extra_params: Any, timezone_name: Any, windows: Any
) -> dict[str, Any]:
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
        timezone_name
        or params.get("availability_timezone")
        or _DEFAULT_AVAILABILITY_TIMEZONE
    )
    params["availability_windows"] = normalized
    return params


def availability_view(extra_params: Any) -> dict[str, Any]:
    """Read the stored schedule for the control panel."""
    params = extra_params if isinstance(extra_params, dict) else {}
    raw_windows = params.get("availability_windows")
    windows: list[dict[str, str]] = []
    if isinstance(raw_windows, list):
        for item in raw_windows:
            if not isinstance(item, dict):
                continue
            start, end = (
                _clock_minutes(item.get("start")),
                _clock_minutes(item.get("end")),
            )
            if start is None or end is None or start == end:
                continue
            windows.append(
                {
                    "start": f"{start // 60:02d}:{start % 60:02d}",
                    "end": f"{end // 60:02d}:{end % 60:02d}",
                }
            )
    timezone_name = str(
        params.get("availability_timezone") or _DEFAULT_AVAILABILITY_TIMEZONE
    )
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = _DEFAULT_AVAILABILITY_TIMEZONE
    return {"availability_timezone": timezone_name, "availability_windows": windows}


def route_available_at(extra_params: Any, moment: datetime | None = None) -> bool:
    """Whether a model may be called at ``moment``.

    No usable windows means always available. Invalid stored values fail open
    so a bad schedule cannot take every route offline. Comparison uses the
    schedule timezone and ignores seconds.
    """
    view = availability_view(extra_params)
    windows = view["availability_windows"]
    if not windows:
        return True
    when = moment or datetime.now(timezone.utc)
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
    payload: dict[str, Any],
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
    names: list[str] = []
    names.extend(re.findall(r"['\"]([A-Za-z][A-Za-z0-9_]{0,40})['\"]", message))
    labeled = re.search(
        r"(?:parameter|参数)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_]{0,40})",
        message,
        re.IGNORECASE,
    )
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


def anthropic_sdk_base_url(base_url: str | None) -> str | None:
    """Base URL for the Anthropic SDK, which appends ``/v1/messages`` itself.

    The model center stores the same ``.../v1`` root used by OpenAI-compatible
    gateways. Leaving that suffix in place would call ``/v1/v1/messages``.
    """
    raw = str(base_url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return raw.rstrip("/") or None
    path = parts.path.rstrip("/")
    if path == "/v1" or path.endswith("/v1"):
        path = path[: -len("/v1")]
    rebuilt = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return rebuilt.rstrip("/") or None


def _openai_base_url_candidates(base_url: str) -> list[str | None]:
    """Try /v1 only when the configured URL has no versioned API path."""
    base_url = base_url.rstrip("/")
    candidates: list[str | None] = [base_url or None]
    last_segment = urlsplit(base_url).path.rstrip("/").rsplit("/", 1)[-1]
    if base_url and not re.fullmatch(r"v\d+(?:[._-].+)?", last_segment, re.IGNORECASE):
        candidates.append(f"{base_url}/v1")
    return candidates
