"""Report reuse eligibility and period rules; selection and writes remain in the service."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from src.domain import Report, ReportStatus

def _normalize_reuse_strategy(value: str) -> str:
    normalized = (value or "auto").strip().lower()
    allowed = {"auto", "safe", "resume", "manual"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise ValueError(f"Invalid reuse_strategy '{value}'. Allowed values: {joined}")
    return normalized


def _is_hard_failure(error_message: Optional[str]) -> bool:
    text = (error_message or "").lower()
    hard_markers = [
        "no data",
        "column",
        "schema",
        "validation",
        "integrity",
        "syntax",
        "json decode",
    ]
    return any(marker in text for marker in hard_markers)


def _is_reusable_v4_report(report: Report) -> bool:
    from src.generation.report_v4.models import METHOD_VERSION
    from src.generation.report_v4.peer_review import PROMPT_VERSION

    metadata = report.metadata_ if isinstance(report.metadata_, dict) else {}
    document = metadata.get("report_document_v4") or {}
    return (
        metadata.get("report_layout") == "report_v4"
        and isinstance(metadata.get("report_document_v4"), dict)
        and (metadata.get("quality_gate") or {}).get("passed") is True
        and metadata.get("method_version") == METHOD_VERSION
        and (document.get("peer_review") or {}).get("prompt_version") == PROMPT_VERSION
        and (document.get("peer_review") or {}).get("status") == "published"
    )


def _status_str(status_value: object) -> str:
    if isinstance(status_value, ReportStatus):
        return status_value.value
    if isinstance(status_value, str):
        text = status_value.strip().lower()
        if text.startswith("reportstatus."):
            return text.split(".", 1)[1]
        return text
    return str(status_value).strip().lower()


def _utc_day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    start = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end
