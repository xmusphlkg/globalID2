"""Control-plane liveness dependencies kept outside HTTP delivery."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text

from src.core import get_database
from src.core.disease_cutover import get_disease_cutover_config
from src.control_plane.runtime import runtime_registry
from src.domain import Task, TaskStatus


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def readiness_payload() -> dict:
    database_ready = False
    queue = {
        "queued": 0,
        "running": 0,
        "oldest_queued_at": None,
        "oldest_queued_age_seconds": None,
    }
    try:
        async with get_database() as db:
            await db.execute(text("SELECT 1"))
            queued_count = await db.scalar(
                select(func.count()).select_from(Task).where(
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
                )
            )
            running_count = await db.scalar(
                select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING)
            )
            oldest_queued_at = await db.scalar(
                select(func.min(Task.created_at)).where(
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
                )
            )
            oldest_queued_at = _utc(oldest_queued_at)
            queue = {
                "queued": int(queued_count or 0),
                "running": int(running_count or 0),
                "oldest_queued_at": oldest_queued_at.isoformat() if oldest_queued_at else None,
                "oldest_queued_age_seconds": (
                    max(0, int((datetime.now(timezone.utc) - oldest_queued_at).total_seconds()))
                    if oldest_queued_at
                    else None
                ),
            }
        database_ready = True
    except Exception:
        pass
    services, registry_available = await runtime_registry.list_services()
    live_services = {str(item.get("service")) for item in services}
    required_services = {
        name: ("ok" if name in live_services else "missing")
        for name in ("worker", "scheduler")
    }
    runtime_ready = registry_available and all(
        state == "ok" for state in required_services.values()
    )
    return {
        "status": "ok" if database_ready and runtime_ready else "degraded",
        "db": "ok" if database_ready else "error",
        "runtime": {
            "registry": "ok" if registry_available else "error",
            "required_services": required_services,
        },
        "task_queue": queue,
        "disease_cutover": get_disease_cutover_config().operational_summary(),
    }


__all__ = ["readiness_payload"]
