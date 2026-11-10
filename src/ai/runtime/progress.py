"""Activity progress projection into parent task history."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from sqlalchemy import func, select

from src.ai.task_progress import displayed_progress, task_type_name
from src.core import get_database, get_logger
from src.domain import Task, TaskWorkbook
from src.domain.ai_work_item import AIWorkItem

from .context import parent_execution_generation

logger = get_logger(__name__)


async def _update_progress(db, task: Task, generation: int) -> None:
    rows = (
        await db.execute(
            select(AIWorkItem.status, func.count())
            .where(
                AIWorkItem.parent_task_uuid == task.task_uuid,
                func.coalesce(
                    AIWorkItem.payload["execution_generation"].as_integer(), 0
                )
                == generation,
            )
            .group_by(AIWorkItem.status)
        )
    ).all()
    progress = displayed_progress(
        task_type=task.task_type,
        status=task.status,
        stored=0,
        counts={str(status): int(count or 0) for status, count in rows},
    )
    if (
        task_type_name(task.status)
        in {
            "pending",
            "queued",
            "running",
            "retrying",
        }
        and int(task.progress or 0) != progress
    ):
        task.set_progress(progress)


async def _project_success(parent: str, generation: int) -> bool:
    """Return whether this hint is consumed; busy parents remain pending."""
    try:
        async with get_database() as db:
            task = await db.scalar(
                select(Task)
                .where(Task.task_uuid == parent)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                # Distinguish a deleted parent from one busy with a business
                # transition. This read never waits for its row lock.
                return (
                    await db.scalar(select(Task.id).where(Task.task_uuid == parent))
                    is None
                )
            if (
                task_type_name(task.task_type) != "agent_workflow"
                or parent_execution_generation(task) != generation
                or task_type_name(task.status)
                not in {
                    "pending",
                    "queued",
                    "running",
                    "retrying",
                }
            ):
                return True
            await _update_progress(db, task, generation)
        return True
    except Exception:
        logger.exception(f"Could not project model work progress for {parent}")
        return False


class SuccessProgressBuffer:
    """Coalesce disposable UI hints without delaying provider cleanup.

    Work items remain the source of truth, including when this bounded buffer
    evicts an old hint or a process exits. Failure history never enters it.
    The dispatcher's normal tick drains it; no additional task is created.
    """

    MAX_PENDING = 1024
    BATCH_SIZE = 16
    INTERVAL_SECONDS = 0.5

    def __init__(self) -> None:
        self._pending: OrderedDict[tuple[str, int], int] = OrderedDict()
        self._revision = 0
        self._next_flush_at = 0.0

    def mark(self, event: dict[str, Any]) -> None:
        parent = str(event.get("parent") or "")
        if not parent or parent == "runtime":
            return
        key = parent, int(event.get("execution_generation") or 0)
        if key not in self._pending and len(self._pending) >= self.MAX_PENDING:
            self._pending.popitem(last=False)
        self._revision += 1
        self._pending[key] = self._revision

    async def flush(self, *, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if not self._pending or (not force and now < self._next_flush_at):
            return
        self._next_flush_at = now + self.INTERVAL_SECONDS
        for key, revision in list(self._pending.items())[: self.BATCH_SIZE]:
            # Rotate busy parents so they cannot starve other pending hints.
            if key not in self._pending:
                continue
            self._pending.move_to_end(key)
            consumed = await _project_success(*key)
            # A completion can arrive during the database read. Its newer hint
            # must survive even if this projection consumed the previous one.
            if consumed and self._pending.get(key) == revision:
                self._pending.pop(key)


async def note_parent_work(event: dict[str, Any]) -> None:
    """Record model-call progress and keep a failure in the parent task log.

    Successful calls only move the parent percentage. A retry or terminal
    failure is written to the workbook, because the work-item row replaces its
    error on the next attempt.
    """

    parent = str(event.get("parent") or "")
    if not parent or parent == "runtime":
        return
    try:
        async with get_database() as db:
            task = (
                await db.execute(
                    select(Task).where(Task.task_uuid == parent).with_for_update()
                )
            ).scalar_one_or_none()
            if task is None or task_type_name(task.task_type) != "agent_workflow":
                return
            generation = parent_execution_generation(task)
            event_generation = int(event.get("execution_generation") or 0)
            if event_generation == generation:
                await _update_progress(db, task, generation)
            error = event.get("error")
            if event.get("record_error") and error:
                state = "failed" if event.get("status") == "failed" else "retry"
                db.add(
                    TaskWorkbook(
                        task_id=task.id,
                        entry_type="error" if state == "failed" else "warning",
                        title=f"Model call {state}",
                        content="\n".join(
                            line
                            for line in (
                                f"work_id: {event.get('work_id')}",
                                f"execution_generation: {event_generation}",
                                f"line: {event.get('line')}",
                                f"agent: {event.get('agent')}"
                                if event.get("agent")
                                else "",
                                f"route: {event.get('route')}"
                                if event.get("route")
                                else "",
                                f"attempt: {event.get('attempt')}",
                                f"error: {str(error)[:1000]}",
                            )
                            if line
                        ),
                        content_type="text",
                        model_used=(
                            str(event.get("route"))[:100]
                            if event.get("route")
                            else None
                        ),
                        success=False,
                        error_message=str(error)[:1000],
                        metadata_={
                            "source": "ai_work_item",
                            "work_id": event.get("work_id"),
                            "status": event.get("status"),
                            "execution_generation": event_generation,
                        },
                    )
                )
    except Exception:
        logger.exception(f"Could not record model work progress for {parent}")


def _work_event(
    current: AIWorkItem, *, route_key: str | None, record_error: bool
) -> dict[str, Any]:
    payload = current.payload if isinstance(current.payload, dict) else {}
    agent = payload.get("agent_name")
    return {
        "parent": current.parent_task_uuid,
        "work_id": current.id,
        "line": current.line,
        "status": current.status,
        "attempt": current.attempt,
        "route": route_key,
        "error": current.error,
        "agent": agent if isinstance(agent, str) and agent.strip() else None,
        "record_error": record_error and bool(current.error),
        "execution_generation": int(payload.get("execution_generation") or 0),
    }
