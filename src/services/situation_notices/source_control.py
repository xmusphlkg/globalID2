"""Per-source situation runs: which feeds a task covers, and how the panel reads them.

A task with no ``sources`` list collects every registered feed, matching the
historical full refresh. A task that names feeds overlaps another active task
only when their sets intersect.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import RunStage, Task, TaskStatus, TaskType
from src.services.global_sources.catalogue import SOURCE_REGISTRY

_ACTIVE = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
)
_STAGE_ORDER = ("collect", "organize", "store")


def normalize_situation_sources(requested: Iterable[str] | None) -> list[str]:
    raw = [str(item or "").strip() for item in (requested or [])]
    raw = [item for item in raw if item]
    if not raw:
        return []
    unknown: list[str] = []
    chosen: list[str] = []
    seen: set[str] = set()
    for source_id in raw:
        if source_id in seen:
            continue
        seen.add(source_id)
        if source_id not in SOURCE_REGISTRY:
            unknown.append(source_id)
        else:
            chosen.append(source_id)
    if unknown:
        raise ValueError("Unknown situation sources: " + ", ".join(unknown))
    return chosen


def task_sources(task: Any) -> list[str]:
    raw = (getattr(task, "input_data", None) or {}).get("sources") or []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def covers(task: Any, source_id: str) -> bool:
    selected = task_sources(task)
    return not selected or source_id in selected


def overlaps(active: Any, requested: list[str]) -> bool:
    existing = task_sources(active)
    if not requested or not existing:
        return True
    return bool(set(requested) & set(existing))


def _status(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _stages(rows: list[Any], task_uuid: str) -> list[dict[str, Any]]:
    if not rows:
        return [
            {"stage": name, "status": None, "attempt": None, "error": None, "task_uuid": task_uuid}
            for name in _STAGE_ORDER
        ]
    attempt = max(int(row.attempt or 0) for row in rows)
    current = {row.stage: row for row in rows if int(row.attempt or 0) == attempt}
    return [
        {
            "stage": name,
            "status": current[name].status if name in current else None,
            "attempt": attempt,
            "error": current[name].error if name in current else None,
            "task_uuid": task_uuid,
        }
        for name in _STAGE_ORDER
    ]


async def reject_overlapping_run(db: AsyncSession, requested: list[str]) -> None:
    from fastapi import HTTPException

    active = (
        await db.execute(
            select(Task)
            .where(
                Task.task_type == TaskType.REFRESH_SITUATION_SOURCES,
                Task.status.in_(_ACTIVE),
            )
            .order_by(Task.created_at.desc())
        )
    ).scalars().all()
    conflict = next((task for task in active if overlaps(task, requested)), None)
    if conflict is not None:
        raise HTTPException(
            409,
            f"A situation collection is already active (task {conflict.task_uuid})",
        )


async def source_monitors(db: AsyncSession) -> dict[str, dict[str, Any]]:
    tasks = (
        await db.execute(
            select(Task)
            .where(Task.task_type == TaskType.REFRESH_SITUATION_SOURCES)
            .order_by(Task.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    uuids = [task.task_uuid for task in tasks]
    grouped: dict[str, list[Any]] = {}
    if uuids:
        rows = (
            await db.execute(
                select(RunStage)
                .where(RunStage.task_uuid.in_(uuids))
                .order_by(RunStage.attempt, RunStage.id)
            )
        ).scalars().all()
        for row in rows:
            grouped.setdefault(row.task_uuid, []).append(row)
    monitors: dict[str, dict[str, Any]] = {}
    for source_id in SOURCE_REGISTRY:
        task = next((item for item in tasks if covers(item, source_id)), None)
        if task is None:
            monitors[source_id] = {
                "latest_task_uuid": None,
                "latest_task_status": None,
                "stages": _stages([], ""),
            }
            continue
        monitors[source_id] = {
            "latest_task_uuid": task.task_uuid,
            "latest_task_status": _status(task.status),
            "stages": _stages(grouped.get(task.task_uuid, []), task.task_uuid),
        }
    return monitors


__all__ = [
    "covers",
    "normalize_situation_sources",
    "overlaps",
    "reject_overlapping_run",
    "source_monitors",
    "task_sources",
]
