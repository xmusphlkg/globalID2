"""Business parent context and provider execution boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from src.core import get_database
from src.domain import Task

from .contracts import FAILURE_THRESHOLDS, AIWorkFailed
from .store import _lock_reservation

_PARENT: ContextVar[ParentContext | None] = ContextVar("ai_work_parent", default=None)
_DISPATCHING: ContextVar[bool] = ContextVar("ai_work_dispatching", default=False)
_WAITING: set[tuple[str, int]] = set()


@dataclass
class ParentContext:
    task_uuid: str
    line: str
    priority: str
    generation: int = 0
    waiting: int = 0
    failures: list[str] = field(default_factory=list)
    work_ids: set[int] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    enqueue_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    invocations: dict[str, int] = field(default_factory=dict)


def in_dispatcher() -> bool:
    return _DISPATCHING.get()


def require_dispatcher() -> None:
    """Provider transports run only inside an admitted worker execution."""
    if not in_dispatcher():
        raise RuntimeError("Model transport requires the worker dispatcher")


def current_parent() -> ParentContext | None:
    return _PARENT.get()


def waiting_parent_uuids() -> set[str]:
    return {task_uuid for task_uuid, _generation in _WAITING}


def parent_execution_generation(task: Any) -> int:
    metadata = getattr(task, "metadata_", None) or {}
    try:
        return max(0, int(metadata.get("ai_execution_generation", 0)))
    except (TypeError, ValueError):
        return 0


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def parent_line(task: Task) -> str:
    operation = (getattr(task, "input_data", None) or {}).get("operation")
    if operation in {"content_governance", "governance_stream", "governance_simple"}:
        return "content_governance"
    if operation == "idle_situation":
        return "situation_notice"
    return {
        "generate_report": "report_section",
        "generate_section": "report_section",
        "review_section": "report_review",
        "update_disease_knowledge": "knowledge_brief",
        "enrich_literature": "literature_enrich",
        "refresh_situation_sources": "situation_notice",
    }.get(_value(task.task_type), _value(task.task_type))


@asynccontextmanager
async def task_context(task: Task):
    existing = current_parent()
    if (
        existing
        and existing.task_uuid == task.task_uuid
        and existing.generation == parent_execution_generation(task)
    ):
        yield existing
        return
    line = parent_line(task)
    priority = _value(getattr(task, "priority", "normal") or "normal")
    inp = getattr(task, "input_data", None) or {}
    if line in {"report_section", "report_review"} and priority != "urgent":
        priority = "high"
    if line == "knowledge_brief" and (
        "disease-knowledge-autopilot"
        in {
            str(inp.get("requested_by") or "").strip().casefold(),
            str(inp.get("initiated_via") or "").strip().casefold(),
        }
        or inp.get("autopilot")
        or inp.get("trigger") == "autopilot"
        or inp.get("trigger_source") == "autopilot"
    ):
        priority = "low"
    state = ParentContext(
        task.task_uuid, line, priority, generation=parent_execution_generation(task)
    )
    token = _PARENT.set(state)
    try:
        yield state
    finally:
        _PARENT.reset(token)
        if state.waiting or (task.task_uuid, state.generation) in _WAITING:
            await _set_waiting(state, False)


async def _set_waiting(state: ParentContext, waiting: bool) -> None:
    async with get_database() as db:
        await _lock_reservation(db, f"parent:{state.task_uuid}")
        task = (
            await db.execute(
                select(Task).where(Task.task_uuid == state.task_uuid).with_for_update()
            )
        ).scalar_one_or_none()
        current = (
            task is not None and parent_execution_generation(task) == state.generation
        )
        if current:
            data = dict(task.input_data or {})
            data["ai_waiting"] = waiting
            task.input_data = data
    identity = (state.task_uuid, state.generation)
    if waiting and current:
        _WAITING.add(identity)
    else:
        _WAITING.discard(identity)


def assert_children_succeeded() -> None:
    state = current_parent()
    if state and FAILURE_THRESHOLDS.get(state.line, 0) is not None and state.failures:
        raise AIWorkFailed(
            f"{state.line}: {len(state.failures)} model work item(s) failed: {state.failures[0]}"
        )
