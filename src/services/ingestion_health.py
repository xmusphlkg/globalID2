"""Small source-quality projections for scheduler health snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from src.domain.collection import RunStage


async def latest_source_audit_statuses(db, task_uuids: Sequence[str]) -> dict[str, str]:
    """Read only audit statuses from each task's current execution attempt.

    Attempt selection includes all stages: a newer collection-only attempt
    must never inherit the previous attempt's organize audit. The projection
    stays small even when audits contain thousands of report details.
    """

    if not task_uuids:
        return {}
    latest_attempts = (
        select(RunStage.task_uuid, func.max(RunStage.attempt).label("attempt"))
        .where(RunStage.task_uuid.in_(task_uuids))
        .group_by(RunStage.task_uuid)
        .subquery()
    )
    statement = (
        select(
            RunStage.task_uuid,
            RunStage.metrics["source_audit"]["status"].as_string(),
        )
        .join(
            latest_attempts,
            (RunStage.task_uuid == latest_attempts.c.task_uuid)
            & (RunStage.attempt == latest_attempts.c.attempt),
        )
        .where(RunStage.stage == "organize", RunStage.status == "completed")
    )
    return {
        task_uuid: status
        for task_uuid, status in (await db.execute(statement)).all()
        if isinstance(status, str)
    }
