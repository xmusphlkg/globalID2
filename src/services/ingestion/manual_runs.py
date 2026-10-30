"""Transactional audit records for reviewed maintenance of existing data.

These operations consume existing inputs rather than downloading sources. They
must not pretend that a fresh collect or organize stage ran. The caller owns the
transaction: formal rows, the completed audit task, stages and dirty markers all
commit or roll back together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import Task, TaskPriority, TaskStatus, TaskType
from src.domain.collection import LINES, RunStage
from .stages import mark_dirty


async def record_manual_store(
    db: AsyncSession,
    *,
    line: str,
    scope_keys: Iterable[str],
    operation: str,
    metrics: dict | None = None,
    input_data: dict | None = None,
) -> Task:
    """Add a completed maintenance run and pending publication scopes; no commit.

    Call only after the maintenance operation has succeeded, in the same
    session as its formal writes. ``scope_keys`` use the normal line contract
    (country codes for surveillance, ``*`` or source scopes for other lines).
    """
    scopes = sorted(set(scope_keys))
    if line not in LINES or not scopes or not operation:
        raise ValueError("A maintenance run needs a valid line, scopes and operation")
    now = datetime.now(timezone.utc)
    task = Task(
        task_uuid=str(uuid4()),
        task_type=TaskType.PROCESS_DATA,
        task_name=f"Maintenance: {operation}",
        status=TaskStatus.COMPLETED,
        priority=TaskPriority.NORMAL,
        progress=100,
        total_steps=1,
        completed_steps=1,
        started_at=now,
        completed_at=now,
        input_data={**(input_data or {}), "operation": operation, "line": line,
                    "scope_keys": scopes, "maintenance": True},
        output_data={"status": "completed", **(metrics or {})},
    )
    db.add(task)
    for stage in ("collect", "organize", "store"):
        db.add(RunStage(
            task_uuid=task.task_uuid,
            stage=stage,
            attempt=1,
            status="completed" if stage == "store" else "skipped",
            started_at=now,
            finished_at=now,
            metrics=(metrics or {}) if stage == "store" else {
                "reason": "maintenance_consumes_existing_inputs",
            },
        ))
    for scope in scopes:
        await mark_dirty(db, line=line, scope_key=scope, task_uuid=task.task_uuid)
    return task


__all__ = ["record_manual_store"]
