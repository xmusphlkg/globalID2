"""Read models used by the Overview and Action Items workspaces."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.control_plane.runtime import runtime_registry
from src.domain import AutomationJob, DataReleaseJob, Task, TaskStatus, TaskType


SUMMARY_LOOKBACK_DAYS = 14
PIPELINE_LOOKBACK_HOURS = 72
NON_OPERATIONAL_TASK_PREFIXES = ("[test]", "[smoke]", "[smoke-slow]")
ACTIVE_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)
INGESTION_TASK_TYPES = (TaskType.CRAWL_DATA, TaskType.PROCESS_DATA)
AI_TASK_TYPES = (
    TaskType.GENERATE_REPORT,
    TaskType.GENERATE_SECTION,
    TaskType.REVIEW_SECTION,
    TaskType.UPDATE_DISEASE_KNOWLEDGE,
    TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
    TaskType.AGENT_WORKFLOW,
    TaskType.SEND_EMAIL,
)
PUBLISHING_TASK_TYPES = (TaskType.EXPORT_DATA,)


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _operational_task_filter():
    return and_(
        *(~func.lower(Task.task_name).like(f"{prefix}%") for prefix in NON_OPERATIONAL_TASK_PREFIXES),
        ~cast(Task.tags, Text).ilike('%"test"%'),
    )


def _failure_signature(task: Task) -> tuple[str, str]:
    error = str(task.last_error or "Task failed without a recorded error.").strip()
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    summary = lines[0] if lines else error
    for line in reversed(lines):
        exception_name, separator, _ = line.partition(":")
        if separator and exception_name.endswith(("Error", "Exception")):
            summary = line
            break
    return task.task_name, summary


def _pipeline_stage_status(
    task_types: tuple[TaskType, ...],
    *,
    failed_types: set[TaskType],
    active_types: set[TaskType],
    idle_when_clear: bool = False,
) -> str:
    if any(task_type in failed_types for task_type in task_types):
        return "attention"
    if any(task_type in active_types for task_type in task_types):
        return "active"
    return "idle" if idle_when_clear else "healthy"


class ControlPlaneOverviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def task_counts(self) -> dict[str, int]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SUMMARY_LOOKBACK_DAYS)
        rows = (
            await self.db.execute(
                select(Task.status, func.count(Task.id))
                .where(
                    _operational_task_filter(),
                    or_(Task.created_at >= cutoff, Task.status.in_(ACTIVE_TASK_STATUSES)),
                )
                .group_by(Task.status)
            )
        ).all()
        counts = {_enum_value(status): int(count or 0) for status, count in rows}
        for status in TaskStatus:
            counts.setdefault(status.value, 0)
        return counts

    async def recent_tasks(self, limit: int = 8) -> list[dict[str, Any]]:
        tasks = (
            await self.db.execute(
                select(Task)
                .where(_operational_task_filter())
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "task_uuid": task.task_uuid,
                "name": task.task_name,
                "type": _enum_value(task.task_type),
                "status": _enum_value(task.status),
                "progress": int(task.progress or 0),
                "created_at": task.created_at,
                "last_error": task.last_error,
            }
            for task in tasks
        ]

    async def action_items(self, limit: int = 20) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SUMMARY_LOOKBACK_DAYS)
        tasks = (
            await self.db.execute(
                select(Task)
                .where(
                    Task.status == TaskStatus.FAILED,
                    Task.created_at >= cutoff,
                    _operational_task_filter(),
                )
                .order_by(Task.created_at.desc())
            )
        ).scalars().all()

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for task in tasks:
            signature = _failure_signature(task)
            if signature in grouped:
                grouped[signature]["count"] += 1
            else:
                grouped[signature] = {"task": task, "count": 1, "cause": signature[1]}

        items = [
            {
                "id": f"task:{group['task'].task_uuid}",
                "severity": (
                    "critical"
                    if _enum_value(group["task"].priority) in {"high", "urgent"}
                    else "warning"
                ),
                "category": "task",
                "title": group["task"].task_name,
                "detail": (
                    f"{group['count']} similar failures in the last {SUMMARY_LOOKBACK_DAYS} days. "
                    f"Root cause: {group['cause']}"
                    if group["count"] > 1
                    else group["cause"]
                ),
                "resource_type": "task",
                "resource_id": group["task"].task_uuid,
                "occurred_at": (
                    group["task"].completed_at
                    or group["task"].updated_at
                    or group["task"].created_at
                ),
                "href": f"/operations/tasks?task={group['task'].task_uuid}",
            }
            for group in grouped.values()
        ]

        services, redis_ok = await runtime_registry.list_services()
        live_kinds = {str(item.get("service")) for item in services}
        for kind in ("worker", "scheduler"):
            if kind not in live_kinds:
                items.insert(
                    0,
                    {
                        "id": f"runtime:{kind}",
                        "severity": "critical",
                        "category": "runtime",
                        "title": f"{kind.title()} is unavailable",
                        "detail": "No current runtime heartbeat was found." if redis_ok else "Runtime heartbeat storage is unavailable.",
                        "resource_type": "runtime",
                        "resource_id": kind,
                        "occurred_at": datetime.now(timezone.utc),
                        "href": "/operations/runtime",
                    },
                )
        return items[:limit]

    async def pipeline_task_types(self) -> tuple[set[TaskType], set[TaskType]]:
        failure_cutoff = datetime.now(timezone.utc) - timedelta(hours=PIPELINE_LOOKBACK_HOURS)
        failed = (
            await self.db.execute(
                select(Task.task_type)
                .where(
                    Task.status == TaskStatus.FAILED,
                    Task.created_at >= failure_cutoff,
                    _operational_task_filter(),
                )
                .distinct()
            )
        ).scalars().all()
        active = (
            await self.db.execute(
                select(Task.task_type)
                .where(
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                    _operational_task_filter(),
                )
                .distinct()
            )
        ).scalars().all()
        return {TaskType(value) for value in failed}, {TaskType(value) for value in active}

    async def schedule_counts(self) -> dict[str, int]:
        automation = int(
            (await self.db.execute(select(func.count()).select_from(AutomationJob))).scalar_one() or 0
        )
        release = int(
            (await self.db.execute(select(func.count()).select_from(DataReleaseJob))).scalar_one() or 0
        )
        enabled_automation = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(AutomationJob).where(AutomationJob.enabled.is_(True))
                )
            ).scalar_one()
            or 0
        )
        enabled_release = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(DataReleaseJob).where(DataReleaseJob.enabled.is_(True))
                )
            ).scalar_one()
            or 0
        )
        return {
            "total": automation + release,
            "enabled": enabled_automation + enabled_release,
            "ingestion": automation,
            "release": release,
        }

    async def overview(self) -> dict[str, Any]:
        task_counts = await self.task_counts()
        schedules = await self.schedule_counts()
        services, heartbeat_available = await runtime_registry.list_services()
        actions = await self.action_items(limit=8)
        failed_types, active_types = await self.pipeline_task_types()
        return {
            "generated_at": datetime.now(timezone.utc),
            "tasks": task_counts,
            "schedules": schedules,
            "runtime": {
                "heartbeat_available": heartbeat_available,
                "services": services,
            },
            "action_items": actions,
            "recent_tasks": await self.recent_tasks(),
            "pipeline": [
                {
                    "id": "ingestion",
                    "label": "Ingestion",
                    "status": _pipeline_stage_status(
                        INGESTION_TASK_TYPES,
                        failed_types=failed_types,
                        active_types=active_types,
                    ),
                },
                {"id": "governance", "label": "Data governance", "status": "healthy"},
                {
                    "id": "ai",
                    "label": "AI production",
                    "status": _pipeline_stage_status(
                        AI_TASK_TYPES,
                        failed_types=failed_types,
                        active_types=active_types,
                        idle_when_clear=True,
                    ),
                },
                {
                    "id": "publishing",
                    "label": "Publishing",
                    "status": _pipeline_stage_status(
                        PUBLISHING_TASK_TYPES,
                        failed_types=failed_types,
                        active_types=active_types,
                    ),
                },
            ],
        }


__all__ = ["ControlPlaneOverviewService"]
