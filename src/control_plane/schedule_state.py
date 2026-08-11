"""Persistence adapter for scheduler state projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from src.core import get_database, get_logger
from src.domain import ScheduledJobState

logger = get_logger(__name__)


class ScheduleStateRepository:
    async def load(self, job_kind: str) -> dict[str, dict[str, Any]]:
        try:
            async with get_database() as db:
                rows = (
                    await db.execute(
                        select(ScheduledJobState).where(ScheduledJobState.job_kind == job_kind)
                    )
                ).scalars().all()
            return {
                row.job_id: {
                    "next_run_at": row.next_run_at,
                    "last_started_at": row.last_started_at,
                    "last_finished_at": row.last_finished_at,
                    "last_status": row.last_status,
                    "last_error": row.last_error,
                    "last_task_uuid": row.last_task_uuid,
                }
                for row in rows
            }
        except DBAPIError as exc:
            logger.warning("Schedule state table is unavailable; run Alembic upgrade: {}", exc)
            return {}

    async def save(self, job_kind: str, job_id: str, state: Any) -> None:
        try:
            async with get_database() as db:
                row = (
                    await db.execute(
                        select(ScheduledJobState).where(
                            ScheduledJobState.job_kind == job_kind,
                            ScheduledJobState.job_id == job_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = ScheduledJobState(job_kind=job_kind, job_id=job_id)
                    db.add(row)
                for field in (
                    "next_run_at",
                    "last_started_at",
                    "last_finished_at",
                    "last_status",
                    "last_error",
                    "last_task_uuid",
                ):
                    setattr(row, field, getattr(state, field, None))
                await db.commit()
        except DBAPIError as exc:
            logger.warning("Unable to persist schedule state: {}", exc)

    async def remove(self, job_kind: str, job_id: str) -> None:
        try:
            async with get_database() as db:
                await db.execute(
                    delete(ScheduledJobState).where(
                        ScheduledJobState.job_kind == job_kind,
                        ScheduledJobState.job_id == job_id,
                    )
                )
                await db.commit()
        except DBAPIError as exc:
            logger.warning("Unable to remove schedule state: {}", exc)


schedule_state_repository = ScheduleStateRepository()

__all__ = ["ScheduleStateRepository", "schedule_state_repository"]
