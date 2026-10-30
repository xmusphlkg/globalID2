"""Durable collect, organize and store stages with transactional publish markers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.domain import PublishDirty, RunStage


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def next_attempt(db: AsyncSession, task_uuid: str) -> int:
    """The attempt number for a fresh execution: 1 if this ``task_uuid`` has no
    ``run_stages`` rows yet, otherwise the highest existing attempt plus one."""

    max_attempt = (
        await db.execute(
            select(func.coalesce(func.max(RunStage.attempt), 0)).where(
                RunStage.task_uuid == task_uuid
            )
        )
    ).scalar_one()
    return int(max_attempt) + 1


async def record_stage(
    db: AsyncSession,
    *,
    task_uuid: str,
    stage: str,
    status: str,
    attempt: int,
    started_at: datetime,
    finished_at: Optional[datetime] = None,
    metrics: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """Add one ``RunStage`` row to the current session.

    The caller computes ``attempt`` once per execution via :func:`next_attempt`
    and reuses it for every stage of that execution.
    """

    db.add(
        RunStage(
            task_uuid=task_uuid,
            stage=stage,
            status=status,
            attempt=attempt,
            started_at=started_at,
            finished_at=finished_at,
            metrics=metrics or {},
            error=error,
        )
    )


__all__ = ["next_attempt", "record_stage"]


async def mark_dirty(db: AsyncSession, *, line: str, scope_key: str, task_uuid: str) -> None:
    """Participate in the formal-row transaction; never commit independently."""
    db.add(PublishDirty(line=line, scope_key=scope_key, source_task_uuid=task_uuid, marked_at=_now()))


class StageRun:
    """Durable three-stage lifecycle, including stages not reached after failure.

    Terminal transitions update the existing row. A retry allocates one new
    attempt at start and preserves every preceding attempt.
    """

    def __init__(self, task_uuid: str, line: str, attempt: int):
        self.task_uuid, self.line, self.attempt = task_uuid, line, attempt
        self.current: str | None = None

    @classmethod
    async def start(cls, task_uuid: str, line: str) -> "StageRun":
        async with get_db() as db:
            attempt = await next_attempt(db, task_uuid)
            for stage in ("collect", "organize", "store"):
                await record_stage(db, task_uuid=task_uuid, stage=stage, status="skipped", attempt=attempt, started_at=_now())
        return cls(task_uuid, line, attempt)

    async def transition(self, stage: str, *, metrics: dict | None = None) -> None:
        from .capture import _current
        if _current.get():
            _current.get().stage = stage
        if self.current:
            await self.finish(metrics=None)
        from sqlalchemy import update
        async with get_db() as db:
            await db.execute(update(RunStage).where(RunStage.task_uuid == self.task_uuid, RunStage.attempt == self.attempt, RunStage.stage == stage).values(status="running", started_at=_now(), finished_at=None, metrics=metrics or {}))
        self.current = stage

    async def finish(self, *, metrics: dict | None = None, error: str | None = None) -> None:
        if not self.current:
            return
        from sqlalchemy import update
        async with get_db() as db:
            row = (await db.execute(select(RunStage).where(RunStage.task_uuid == self.task_uuid, RunStage.attempt == self.attempt, RunStage.stage == self.current))).scalar_one()
            row.status = "failed" if error else "completed"
            row.finished_at = _now()
            row.error = error
            if metrics:
                row.metrics = {**(row.metrics or {}), **metrics}
        self.current = None

    async def mark_dirty(self, db: AsyncSession, scope_key: str) -> None:
        await mark_dirty(db, line=self.line, scope_key=scope_key, task_uuid=self.task_uuid)


__all__ += ["StageRun", "mark_dirty"]
