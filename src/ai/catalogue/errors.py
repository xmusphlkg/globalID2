"""Provider failure classification shared by execution, routing health and probes.

Classifiers do not mutate route state or retry a request. Their output separates
credential scope, model availability, temporary transport failures and request
errors so the caller can make one bounded retry decision.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from math import ceil
from typing import Any, Literal

_ROUTE_CHANNEL_FAILURE_CODES = {"get_channel_failed"}
_EDGE_BLOCK_RETRY_SECONDS = 60


class FailureKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    MODEL_UNAVAILABLE = "model_unavailable"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    UPSTREAM = "upstream"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderFailure:
    kind: FailureKind
    retryable: bool
    scope: Literal["provider", "model", "request"]
    retry_after_seconds: int | None = None


def classify_failure(error: Any) -> ProviderFailure:
    """Classify one provider attempt; unknown application errors stay unknown.

    Authentication takes precedence over message-based quota detection. A 401
    can mention a quota or retry hint while still invalidating the credential.
    A model-specific 403 never invalidates healthy sibling models.
    """
    if is_provider_authentication_error(error):
        return ProviderFailure(FailureKind.AUTHENTICATION, False, "provider")
    if _is_cloudflare_edge_block(error):
        return ProviderFailure(
            FailureKind.UPSTREAM,
            True,
            "model",
            max(_EDGE_BLOCK_RETRY_SECONDS, extract_retry_after_seconds(error) or 0),
        )
    if is_model_channel_failure(error):
        return ProviderFailure(FailureKind.CHANNEL_UNAVAILABLE, True, "model")
    if is_rate_limit_error(error):
        return ProviderFailure(
            FailureKind.RATE_LIMIT, True, "model", extract_retry_after_seconds(error)
        )
    if is_model_unavailable_error(error):
        return ProviderFailure(FailureKind.MODEL_UNAVAILABLE, False, "model")
    transport = _runtime_failure_kind(error)
    if transport is not None:
        return ProviderFailure(FailureKind(transport), True, "model")
    invalid_request = _http_status(error) in {400, 422}
    if invalid_request:
        return ProviderFailure(FailureKind.INVALID_REQUEST, False, "request")
    return ProviderFailure(FailureKind.UNKNOWN, False, "request")


def _http_status(error: Any) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_cloudflare_edge_block(error: Any) -> bool:
    """Identify an HTML edge rejection, never a provider's JSON permission error.

    A Cloudflare response header alone also appears on successful provider API
    traffic. Require HTTP 403 and its specific HTML error/challenge page. The
    block may persist, so callers still enforce the ordinary attempt budget.
    """
    if _http_status(error) != 403:
        return False
    body = getattr(error, "body", None)
    if isinstance(body, (dict, list)):
        return False
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    if "json" in str(headers.get("content-type", "")).lower():
        return False
    if not isinstance(body, str):
        try:
            body = getattr(response, "text", None)
        except RuntimeError:  # An unread streaming response has no text yet.
            body = None
    page = (body if isinstance(body, str) and body else str(error)).lower()
    if not re.search(r"<!doctype\s+html\b|<html(?:\s|>)", page):
        return False
    return bool(
        re.search(
            r"<title[^>]*>\s*attention required!\s*\|\s*cloudflare\s*</title>",
            page,
        )
        or (
            re.search(r"<title[^>]*>\s*just a moment[.!\s]*</title>", page)
            and ("/cdn-cgi/challenge-platform/" in page or "cf-chl-" in page)
        )
    )


def is_rate_limit_error(error: Any) -> bool:
    normalized_status = _http_status(error)

    if normalized_status == 429:
        return True

    # Some providers return throttling as HTTP 400 with provider-specific error code.
    code_candidates = _extract_error_codes(error)
    message = str(error)

    if any(
        code
        in {
            "429",
            "2003",
            "insufficient_quota",
            "quota_exceeded",
            "rate_limit_exceeded",
        }
        for code in code_candidates
    ):
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
    normalized_status = _http_status(error)

    if normalized_status == 401:
        return True
    if normalized_status == 403:
        # Preserve the existing model-level ACL fallback behaviour. A provider
        # that returns 403 for one restricted model may still serve siblings.
        return False

    code_candidates = _extract_error_codes(error)
    if any(
        code
        in {
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


def _extract_error_codes(error: Any) -> list[str]:
    codes: list[str] = []

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
    if (
        is_provider_authentication_error(error)
        or is_model_channel_failure(error)
        or _is_cloudflare_edge_block(error)
    ):
        return False

    normalized_status = _http_status(error)

    code_candidates = _extract_error_codes(error)
    if any(
        code in {"model_not_found", "invalid_model", "model_access_denied"}
        for code in code_candidates
    ):
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
    code_candidates = _extract_error_codes(error)
    if any(code in _ROUTE_CHANNEL_FAILURE_CODES for code in code_candidates):
        return True

    # Gateways can use model_not_found even for a temporary routing failure.
    normalized_status = _http_status(error)

    if normalized_status in {500, 503, None}:
        message = str(error).lower()
        return "可用渠道不存在" in str(error) or "no available channel" in message

    return False


def extract_retry_after_seconds(
    error: Any, *, now: datetime | None = None
) -> int | None:
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
                return max(1, ceil(float(str(retry_after).strip())))
            except (TypeError, ValueError, OverflowError):
                try:
                    target = parsedate_to_datetime(str(retry_after))
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    return max(
                        1,
                        ceil(
                            (
                                target - (now or datetime.now(timezone.utc))
                            ).total_seconds()
                        ),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass

    message = str(error)
    patterns = [
        r"retry after\s+(\d+(?:\.\d+)?)s",
        r"try again in\s+(\d+(?:\.\d+)?)s",
        r"in\s+(\d+(?:\.\d+)?)\s*seconds",
        r"after\s+(\d+(?:\.\d+)?)\s*seconds",
        # Pooled gateways: "the pool resumes in 6050s".
        r"resumes?\s+in\s+(\d+(?:\.\d+)?)\s*s\b",
        r"(?:please\s*)?retry\s*(?:in\s*)?(\d+(?:\.\d+)?)\s*s",
        r"(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u518d\u8bd5|\u91cd\u8bd5)",
        r"\u8bf7\u5728\s*(\d+(?:\.\d+)?)\s*\u79d2\s*(?:\u540e)?\s*(?:\u91cd\u8bd5|\u518d\u8bd5)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            try:
                return max(1, ceil(float(match.group(1))))
            except (TypeError, ValueError):
                continue
    return None


def _runtime_failure_kind(error: Any) -> str | None:
    """Classify only transport failures that justify a routing circuit break."""
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if _is_cloudflare_edge_block(error):
        return "upstream"
    status = _http_status(error)
    if status is not None and status >= 500:
        return "upstream"
    if isinstance(error, ConnectionError):
        return "connection"

    message = str(error or "").lower()
    if any(
        marker in message
        for marker in ("timeout", "timed out", "readtimeout", "connecttimeout")
    ):
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


def _health_check_failure_status(error: BaseException) -> str:
    """Map one probe result to a control-plane status without overclaiming.

    Only explicit credential rejection and model-specific permanent errors may
    mark configuration unavailable. Rate limits have their own cooldown; all
    other probe failures are observations, not proof that a route is invalid.
    """
    failure = classify_failure(error)
    if failure.kind is FailureKind.RATE_LIMIT:
        return "rate_limited"
    if "api key is not configured" in str(error or "").lower():
        return "unavailable"
    if failure.kind in {FailureKind.AUTHENTICATION, FailureKind.MODEL_UNAVAILABLE}:
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
            "no available channel",
            "可用渠道不存在",
        )
    )
