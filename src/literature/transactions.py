"""Commit literature facts and their publication marker as one transaction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import Task, TaskPriority, TaskStatus, TaskType


class LiteratureCommitError(RuntimeError):
    """A formal write failed; callers must not treat it as a provider failure."""


async def commit_literature_change(
    db: AsyncSession,
    *,
    operation: str,
    task_uuid: str | None = None,
    actor: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Commit facts, dirty marker and any synchronous operation record together.

    Worker callers supply their real parent task or inherit the worker context.
    Direct API/CLI writes create a completed PROCESS_DATA audit task in this
    transaction. They never enqueue a second execution or use an orphan UUID.
    Any failure rolls back even if the caller catches the exception afterwards.
    """
    from src.ai.work_queue import current_parent
    from src.services.ingestion.stages import mark_dirty

    try:
        if task_uuid is None:
            parent = current_parent()
            task_uuid = parent.task_uuid if parent else None
        if task_uuid is not None:
            existing = (
                await db.execute(select(Task.task_uuid).where(Task.task_uuid == task_uuid))
            ).scalar_one_or_none()
            if existing is None:
                raise ValueError(f"Literature source task does not exist: {task_uuid}")
        else:
            task_uuid = str(uuid4())
            now = datetime.now(timezone.utc)
            db.add(Task(
                task_uuid=task_uuid,
                task_type=TaskType.PROCESS_DATA,
                task_name=f"Literature: {operation}",
                status=TaskStatus.COMPLETED,
                priority=TaskPriority.NORMAL,
                started_at=now,
                completed_at=now,
                progress=100,
                completed_steps=1,
                total_steps=1,
                input_data={
                    "operation": operation,
                    "line": "literature",
                    "actor": actor,
                    "details": details or {},
                },
                output_data={"line": "literature", "scope_key": "*"},
                metadata_={"execution_mode": "synchronous", "audit_record": True},
            ))
        await mark_dirty(db, line="literature", scope_key="*", task_uuid=task_uuid)
        await db.commit()
        return task_uuid
    except BaseException as exc:
        await db.rollback()
        if isinstance(exc, Exception):
            raise LiteratureCommitError(f"Literature transaction failed: {exc}") from exc
        raise
