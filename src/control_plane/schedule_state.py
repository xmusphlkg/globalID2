"""Persistence adapter for scheduler state projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core import get_database, get_logger
from src.domain import ScheduledJobState

logger = get_logger(__name__)


class ScheduleStateRepository:
    async def reschedule(
        self, job_kind: str, job_id: str, next_run_at: Any,
        *, only_if_missing: bool = False,
    ) -> None:
        """Change only the schedule; initialization never overwrites a row."""
        try:
            async with get_database() as db:
                predicate = (
                    ScheduledJobState.job_kind == job_kind,
                    ScheduledJobState.job_id == job_id,
                )
                if only_if_missing:
                    exists = (await db.execute(
                        select(ScheduledJobState.id).where(*predicate)
                    )).scalar_one_or_none()
                    if exists is not None:
                        return
                else:
                    result = await db.execute(update(ScheduledJobState).where(
                        *predicate
                    ).values(next_run_at=next_run_at))
                    if result.rowcount:
                        await db.commit()
                        return
                try:
                    async with db.begin_nested():
                        db.add(ScheduledJobState(
                            job_kind=job_kind, job_id=job_id, next_run_at=next_run_at,
                        ))
                        await db.flush()
                except IntegrityError:
                    # Another process created it after the existence check.
                    # Initializers leave its schedule intact; explicit panel
                    # edits still apply, without touching its execution fields.
                    if not only_if_missing:
                        await db.execute(update(ScheduledJobState).where(
                            *predicate
                        ).values(next_run_at=next_run_at))
                await db.commit()
        except DBAPIError as exc:
            logger.warning("Unable to reschedule job state: {}", exc)

    async def save_result(
        self, job_kind: str, job_id: str, state: Any,
        *, expected_next_run_at: Any,
    ) -> Any:
        """Store dispatch evidence without undoing a concurrent panel edit.

        The comparison and next-run update happen in one SQL statement, also
        when either schedule is NULL (paused). Other scheduler kinds retain
        the existing save contract.
        """
        try:
            async with get_database() as db:
                fields = {field: getattr(state, field, None) for field in (
                    "last_started_at", "last_finished_at", "last_status",
                    "last_error", "last_task_uuid",
                )}
                fields["next_run_at"] = case(
                    (ScheduledJobState.next_run_at.is_not_distinct_from(
                        expected_next_run_at
                    ), state.next_run_at),
                    else_=ScheduledJobState.next_run_at,
                )
                result = await db.execute(update(ScheduledJobState).where(
                    ScheduledJobState.job_kind == job_kind,
                    ScheduledJobState.job_id == job_id,
                ).values(**fields).returning(ScheduledJobState.next_run_at))
                next_run_at = result.scalar_one_or_none()
                await db.commit()
                return next_run_at
        except DBAPIError as exc:
            logger.warning("Unable to persist job dispatch result: {}", exc)
            return state.next_run_at

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

    async def schedule_earlier(
        self,
        job_kind: str,
        job_id: str,
        next_run_at: Any,
    ) -> bool:
        """Atomically pull a persisted run forward without overwriting worker state."""

        try:
            async with get_database() as db:
                result = await db.execute(
                    update(ScheduledJobState)
                    .where(
                        ScheduledJobState.job_kind == job_kind,
                        ScheduledJobState.job_id == job_id,
                        or_(
                            ScheduledJobState.next_run_at.is_(None),
                            ScheduledJobState.next_run_at > next_run_at,
                        ),
                    )
                    .values(next_run_at=next_run_at)
                )
                await db.commit()
                return bool(result.rowcount)
        except DBAPIError as exc:
            logger.warning("Unable to advance schedule state: {}", exc)
            return False

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
