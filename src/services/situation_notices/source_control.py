"""Per-source situation runs: which feeds a task covers, and how the panel reads them.

A task with no ``sources`` list collects every registered feed, matching the
historical full refresh. A task that names feeds overlaps another active task
only when their sets intersect. A task with ``situation_role`` ``read`` covers
no feed and does not block sampling.
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


def panel_run_plan(mode: str) -> dict[str, Any]:
    """Map a control-panel action onto the same roles as the scheduler.

    A sample stores one or more feeds and does not rebuild outbreaks. A read
    task extracts stored reports and rebuilds. A refresh still collects every
    requested feed and extracts in that same task. None of these tasks export;
    new or changed reports mark the situation JSON channel instead.
    """

    plans = {
        "metadata_only": {
            "situation_role": "sample",
            "fetch": True,
            "skip_ai": True,
            "rebuild": False,
            "export": False,
        },
        "no_fetch": {
            "situation_role": "read",
            "fetch": False,
            "skip_ai": False,
            "rebuild": True,
            "export": False,
        },
        "full": {
            "situation_role": "refresh",
            "fetch": True,
            "skip_ai": False,
            "rebuild": True,
            "export": False,
        },
    }
    try:
        return dict(plans[mode])
    except KeyError as exc:
        raise ValueError(f"Unknown situation run mode: {mode}") from exc


def schedule_fields(jobs: Iterable[dict[str, Any]], source_id: str) -> dict[str, Any]:
    """Attach one source's sample slot and the shared reading time."""

    rows = list(jobs)
    sample = next((job for job in rows if job.get("job_id") == f"situation-sample-{source_id}"), None)
    reading = next((job for job in rows if job.get("job_id") == "situation-read"), None)

    def iso(value: Any) -> str | None:
        if value is None:
            return None
        formatter = getattr(value, "isoformat", None)
        return formatter() if callable(formatter) else str(value)

    return {
        "next_sample_at": iso(sample.get("next_run_at")) if sample else None,
        "phase_minute": sample.get("phase_minute") if sample else None,
        "interval_minutes": sample.get("interval_minutes") if sample else None,
        "upstream": sample.get("upstream") if sample else None,
        "schedule_status": sample.get("last_status") if sample else None,
        "sample_pool_size": sample.get("sample_pool_size") if sample else None,
        "read_next_run_at": iso(reading.get("next_run_at")) if reading else None,
        "read_interval_minutes": reading.get("interval_minutes") if reading else None,
    }


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


def _role(task: Any) -> str:
    return str((getattr(task, "input_data", None) or {}).get("situation_role") or "")


def covers(task: Any, source_id: str) -> bool:
    # A reading task does not collect feeds, so it must not block a sample.
    if _role(task) == "read":
        return False
    selected = task_sources(task)
    return not selected or source_id in selected


def overlaps(active: Any, requested: list[str]) -> bool:
    if _role(active) == "read":
        return False
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
    "panel_run_plan",
    "reject_overlapping_run",
    "schedule_fields",
    "source_monitors",
    "task_sources",
]
