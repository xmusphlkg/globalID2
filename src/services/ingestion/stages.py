"""Stage and publish-dirty recording for the data-line ingestion pipeline.

Every ``CRAWL_DATA`` execution leaves one ``RunStage`` per stage (``collect`` /
``organize`` / ``store``) and, after a successful store, a ``publish_dirty``
row so the next ``EXPORT_DATA`` recomputes only that scope.

Until a country switches to the three-stage contract its legacy ``_execute_*``
handler still does all three steps inside ``collect``, so the fused recorder
marks ``collect`` with the run outcome (``metrics.fused=true``) and records
``organize``/``store`` as ``skipped``.  Stage-write failures are logged with
the exact ``collection stage write failed`` message and swallowed so they never
change the handler's success or failure (plan section 5 exemption for fused
countries).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.logging import get_logger
from src.domain import PublishDirty, RunStage

logger = get_logger(__name__)

_STAGE_WRITE_FAILED = "collection stage write failed"


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


async def record_fused_outcome(
    *,
    task_uuid: str,
    started_at: datetime,
    outcome: str,
    error: Optional[str] = None,
    line: str = "surveillance",
    scope_key: Optional[str] = None,
) -> bool:
    """Record the three stages (and optional dirty mark) of a fused run.

    ``outcome`` is ``completed`` or ``failed``.  ``collect`` carries the
    outcome with ``metrics.fused=true``; ``organize`` and ``store`` are
    ``skipped``.  When ``scope_key`` is given a ``publish_dirty`` row is added
    in the same transaction so a stored scope is not lost to a stage failure.

    Returns ``False`` after logging if any write fails; the caller swallows it.
    """

    try:
        async with get_db() as db:
            attempt = await next_attempt(db, task_uuid)
            finished_at = _now()
            await record_stage(
                db,
                task_uuid=task_uuid,
                stage="collect",
                status=outcome,
                attempt=attempt,
                started_at=started_at,
                finished_at=finished_at,
                metrics={"fused": True},
                error=error,
            )
            for stage in ("organize", "store"):
                await record_stage(
                    db,
                    task_uuid=task_uuid,
                    stage=stage,
                    status="skipped",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            if scope_key is not None and outcome == "completed":
                db.add(
                    PublishDirty(
                        line=line,
                        scope_key=scope_key,
                        source_task_uuid=task_uuid,
                        marked_at=finished_at,
                    )
                )
        return True
    except Exception:
        logger.exception(_STAGE_WRITE_FAILED)
        return False


__all__ = [
    "next_attempt",
    "record_stage",
    "record_fused_outcome",
]
