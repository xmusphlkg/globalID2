"""Task-log lines for one situation feed.

One workbook entry per feed records whether collection succeeded, was skipped,
or failed, plus the fetched count. The text stays on the task log drawer.
"""

from __future__ import annotations

from typing import Any

from src.core import get_logger

logger = get_logger(__name__)


async def log_situation_event(
    task_uuid: str | None,
    *,
    title: str,
    content: str,
    entry_type: str = "info",
    success: bool = True,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not task_uuid:
        return
    try:
        from src.core.task_manager import task_manager

        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type=entry_type,
            title=title,
            content=content,
            content_type="text",
            success=success,
            error_message=None if success else content[:500],
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001 - logging must not abort a finished collect
        logger.warning(  # noqa: PLE1205 - get_logger returns a brace-format logger.
            "Situation source log skipped error_type={}",
            type(exc).__name__ or "Exception",
        )


async def log_situation_sources(task_uuid: str | None, statuses: list[dict[str, Any]]) -> None:
    for status in statuses:
        source_id = str(status.get("source_id") or "unknown")
        state = str(status.get("status") or "unknown")
        fetched = int(status.get("event_count") or 0)
        error = status.get("error")
        if state == "failed":
            title = f"Situation source failed: {source_id}"
            entry_type = "error"
            success = False
        elif state == "skipped":
            title = f"Situation source skipped: {source_id}"
            entry_type = "warning"
            success = True
        else:
            title = f"Situation source collected: {source_id}"
            entry_type = "success"
            success = True
        detail = f"{source_id} {state}; fetched {fetched}."
        if error:
            detail = f"{detail} {str(error)[:200]}"
        await log_situation_event(
            task_uuid,
            title=title,
            content=detail,
            entry_type=entry_type,
            success=success,
            metadata={
                "event": "situation_source",
                "source": source_id,
                "status": state,
                "fetched": fetched,
            },
        )


__all__ = ["log_situation_event", "log_situation_sources"]
