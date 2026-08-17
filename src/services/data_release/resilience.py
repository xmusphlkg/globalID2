"""Conservative failure classification for unattended data releases.

Automatic retry is an allow-list: only well-known transient transport/service
failures are retried.  Validation, code, contract, configuration, and
credential failures deliberately remain terminal so automation cannot publish
around a broken safety gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


AUTOMATIC_RELEASE_TRIGGERS = frozenset({"scheduled", "upstream_completion"})

_EXTERNAL_RETRY_STAGES = frozenset(
    {
        "refresh_situation_room",
        "raw_git_archive_publish",
        "github_direct_download_publish",
        "subscription_options_sync",
        "cloudflare_pages_deploy",
        "cloudflare_production_verification",
        "situation_alert_dispatch",
    }
)

_PERMANENT_PATTERNS = (
    r"\bsyntax\s*error\b",
    r"\btype\s*error\b",
    r"\bname\s*error\b",
    r"\bmodule\s*not\s*found\b",
    r"\bno such file or directory\b",
    r"\bcommand not found\b",
    r"\bmissing\b.*\b(token|credential|account|project|repository|config|environment)\b",
    r"\b(invalid|expired)\b.*\b(token|credential|key|secret)\b",
    r"\b(authentication|authorization)\b.*\b(failed|required)\b",
    r"\b(unauthorized|forbidden)\b",
    r"\bhttp\s*(401|403)\b",
    r"\b(status|code)\s*(401|403)\b",
    r"\bcontract\b",
    r"\bschema\b",
    r"\brelease gate\b",
    r"\bvalidation\b.*\bfailed\b",
    r"\bconfiguration\b.*\b(failed|invalid|missing)\b",
    r"\bpreflight\b.*\bsyntax\b",
)

_TRANSIENT_PATTERNS = (
    r"\btemporar(?:y|ily)\b",
    r"\btry again\b",
    r"\btimed?\s*out\b",
    r"\btimeout\b",
    r"\bconnection\s*(?:reset|refused|closed|aborted)\b",
    r"\bcould not resolve\b",
    r"\bname or service not known\b",
    r"\bnetwork (?:error|failure|unreachable)\b",
    r"\bsocket hang up\b",
    r"\b(?:econnreset|econnrefused|etimedout|eai_again)\b",
    r"\bunexpected eof\b",
    r"\btls handshake timeout\b",
    r"\bfetch failed\b",
    r"\bworker_transport_error\b",
    r"\bworker_http_error:(?:429|5\d\d)\b",
    r"\btoo many requests\b",
    r"\brate limit(?:ed|ing)?\b",
    r"\bhttp\s*429\b",
    r"\b(status|code)\s*429\b",
    r"\bhttp\s*5\d\d\b",
    r"\b(status|code)\s*5\d\d\b",
    r"\binternal server error\b",
    r"\bbad gateway\b",
    r"\bservice unavailable\b",
    r"\bgateway timeout\b",
)


@dataclass(frozen=True)
class ReleaseFailureClassification:
    retryable: bool
    category: str
    stage: str | None
    reason: str


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_release_failure(exc: BaseException) -> ReleaseFailureClassification:
    """Classify one release exception without optimistic guessing."""

    stage_value: Any = getattr(exc, "release_stage", None)
    stage = str(stage_value).strip() if stage_value else None
    text = str(exc).strip()
    normalized = text.casefold()

    if _matches(_PERMANENT_PATTERNS, normalized):
        return ReleaseFailureClassification(
            retryable=False,
            category="permanent",
            stage=stage,
            reason="code, contract, configuration, validation, or credential failure",
        )

    # A preflight exception is retryable only when its retained integration
    # diagnostics contain a positive transient signature.  Generic preflight
    # blockers never enter a retry loop.
    if stage == "release_preflight":
        if _matches(_TRANSIENT_PATTERNS, normalized):
            return ReleaseFailureClassification(
                retryable=True,
                category="transient_external",
                stage=stage,
                reason="transient external integration preflight failure",
            )
        return ReleaseFailureClassification(
            retryable=False,
            category="preflight",
            stage=stage,
            reason="preflight failure without a recognized transient signature",
        )

    if stage not in _EXTERNAL_RETRY_STAGES:
        return ReleaseFailureClassification(
            retryable=False,
            category="non_external_stage",
            stage=stage,
            reason="failure did not occur in an allow-listed external release stage",
        )

    if bool(getattr(exc, "timed_out", False)) or _matches(_TRANSIENT_PATTERNS, normalized):
        return ReleaseFailureClassification(
            retryable=True,
            category="transient_external",
            stage=stage,
            reason="recognized transient transport, timeout, rate-limit, or upstream service failure",
        )

    return ReleaseFailureClassification(
        retryable=False,
        category="unclassified_external",
        stage=stage,
        reason="external-stage failure lacked a recognized transient signature",
    )


def automatic_trigger_eligible(input_data: dict[str, Any]) -> bool:
    """Return whether a release task is allowed to retry without an operator."""

    trigger = str(input_data.get("trigger") or "").strip()
    return (
        trigger in AUTOMATIC_RELEASE_TRIGGERS
        and input_data.get("manual_trigger") is not True
    )
