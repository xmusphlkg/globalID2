"""Conservative retry classification for unattended surveillance ingestion.

Scheduled crawls are safe to repeat because source writes are idempotent, but
only failures that are likely to heal without a code or data-policy change may
be retried.  Contract/schema drift, quality-gate failures, credentials, mapping
problems, and programming errors remain terminal and alert an operator.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_PERMANENT_PATTERNS = (
    r"\bsyntax\s*error\b",
    r"\btype\s*error\b",
    r"\bname\s*error\b",
    r"\bmodule\s*not\s*found\b",
    r"\bno such file or directory\b",
    r"\bcommand not found\b",
    r"\bquality gate\b",
    r"\bcontract (?:changed|violation|error)\b",
    r"\bschema\b",
    r"\bunsupported country\b",
    r"\b(conflicting|duplicate) source rows\b",
    r"\b(mapping|registry).*(?:failed|incomplete|not synchronized|ambiguous)\b",
    r"\b(?:invalid|unknown) (?:time|month|date|column|field|dimension|member)\b",
    r"\bmust contain exactly\b",
    r"\bdid not expose .*selector\b",
    r"\btable discovery returned 0 matches\b",
    r"\bvalue too long for type\b",
    r"\b(?:authentication|authorization).*(?:failed|required)\b",
    r"\b(?:unauthorized|forbidden)\b",
    r"\bhttp\s*(?:401|403)\b",
    r"\b(?:status|code)\s*(?:401|403)\b",
    r"\bmissing\b.*\b(?:token|credential|key|secret|config|environment)\b",
    r"\b(?:invalid|expired)\b.*\b(?:token|credential|key|secret)\b",
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
    r"\bmax retries exceeded\b",
    r"\bsocket hang up\b",
    r"\b(?:econnreset|econnrefused|etimedout|eai_again)\b",
    r"\bunexpected[_ ]eof\b",
    r"\btls handshake timeout\b",
    r"\bfetch failed\b",
    r"\btoo many requests\b",
    r"\brate limit(?:ed|ing)?\b",
    r"\bhttp\s*429\b",
    r"\b(?:status|code)\s*429\b",
    r"\bhttp\s*5\d\d\b",
    r"\b(?:status|code)\s*5\d\d\b",
    r"\binternal server error\b",
    r"\bbad gateway\b",
    r"\bservice unavailable\b",
    r"\bgateway timeout\b",
)

# A successful transport that returns an impossible empty surveillance payload
# must not become a successful task.  It may be an upstream publication race or
# edge-cache failure, so scheduled ingestion gets bounded retries.  Discovery
# and schema emptiness are excluded above because they indicate adapter drift.
_UPSTREAM_EMPTY_PATTERNS = (
    r"\b(?:source|api|crawler|cube|workbook)\b.*\b(?:returned|produced|contains) no\b.*\brows\b",
    r"\bno (?:national |complete |usable )?(?:monthly |weekly |case |data )?rows (?:parsed|prepared|remained)\b",
    r"\bno data rows parsed\b",
    r"\bempty (?:monthly )?(?:table|workbook|payload|response)\b",
)


@dataclass(frozen=True)
class IngestionFailureClassification:
    retryable: bool
    category: str
    reason: str


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _exception_text(exc: BaseException) -> str:
    """Include bounded chained diagnostics without exposing object reprs."""

    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(messages) < 4:
        seen.add(id(current))
        message = str(current).strip()
        if message:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return " | ".join(messages)[-12000:]


def classify_ingestion_failure(exc: BaseException) -> IngestionFailureClassification:
    """Classify a crawl failure using a fail-closed allow-list."""

    text = _exception_text(exc).casefold()
    if _matches(_PERMANENT_PATTERNS, text):
        return IngestionFailureClassification(
            retryable=False,
            category="permanent",
            reason="code, source-contract, schema, quality, mapping, configuration, or credential failure",
        )
    if _matches(_TRANSIENT_PATTERNS, text):
        return IngestionFailureClassification(
            retryable=True,
            category="transient_transport",
            reason="recognized transient transport, timeout, rate-limit, or upstream service failure",
        )
    if _matches(_UPSTREAM_EMPTY_PATTERNS, text):
        return IngestionFailureClassification(
            retryable=True,
            category="transient_upstream_empty",
            reason="official source returned an impossible empty data payload",
        )
    return IngestionFailureClassification(
        retryable=False,
        category="unclassified",
        reason="failure lacked a recognized safe automatic-retry signature",
    )


def automatic_ingestion_trigger_eligible(input_data: dict[str, Any]) -> bool:
    """Only scheduler-created crawl tasks may retry without an operator."""

    return bool(
        str(input_data.get("automation_job_id") or "").strip()
        and input_data.get("scheduled_trigger") is True
        and input_data.get("manual_trigger") is not True
    )


__all__ = [
    "IngestionFailureClassification",
    "automatic_ingestion_trigger_eligible",
    "classify_ingestion_failure",
]
