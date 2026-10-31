"""Task-log lines for one literature provider.

Entries name the provider, the record count and the exception type. They do not
include request URLs, queries or credentials.
"""

from __future__ import annotations

from typing import Any

from src.core import get_logger
from src.core.task_manager import task_manager

logger = get_logger(__name__)


async def log_literature_event(
    task_uuid: str | None,
    *,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not task_uuid:
        return
    try:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title=title,
            content=content,
            content_type="text",
            success=True,
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001 - a missing task must not fail the queue
        logger.warning(  # noqa: PLE1205 - get_logger returns a brace-format logger.
            "Literature source log skipped error_type={}",
            type(exc).__name__ or "Exception",
        )


async def log_literature_source(
    task: Any,
    source: str,
    *,
    fetched: int,
    error: str | None = None,
    skipped: str | None = None,
) -> None:
    task_uuid = getattr(task, "task_uuid", None)
    if not task_uuid:
        return
    if skipped:
        title = f"Literature source skipped: {source}"
        content = f"{source} skipped. {skipped}"
        entry_type = "warning"
        success = True
    elif error:
        title = f"Literature source failed: {source}"
        content = f"{source} failed with {error}. Fetched {int(fetched)}."
        entry_type = "error"
        success = False
    else:
        title = f"Literature source collected: {source}"
        content = f"{source} fetched {int(fetched)} record(s)."
        entry_type = "success"
        success = True
    try:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type=entry_type,
            title=title,
            content=content,
            content_type="text",
            success=success,
            error_message=error,
            metadata={
                "event": "literature_source",
                "source": source,
                "fetched": int(fetched),
                "error_type": error,
                "skipped": skipped,
            },
        )
    except Exception as exc:  # noqa: BLE001 - a missing task must not fail collection
        logger.warning(  # noqa: PLE1205 - get_logger returns a brace-format logger.
            "Literature source log skipped source={} error_type={}",
            source,
            type(exc).__name__ or "Exception",
        )


__all__ = ["log_literature_event", "log_literature_source"]
