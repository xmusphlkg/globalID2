"""Bounded reconciliation for legacy, unbound ingest runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from src.domain import LiteratureIngestRun


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def reconcile_stale_unbound_runs(
    db: Any,
    *,
    stale_after_minutes: float,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, int | float | str | bool]:
    """Inspect or terminalize legacy unbound runs using aggregate output only."""
    if stale_after_minutes < 30:
        raise ValueError("stale_after_minutes must be at least 30")
    observed_at = _utc(now or datetime.now(timezone.utc))
    cutoff = observed_at - timedelta(minutes=stale_after_minutes)
    predicate = (
        LiteratureIngestRun.task_uuid.is_(None),
        LiteratureIngestRun.status == "running",
        LiteratureIngestRun.started_at <= cutoff,
    )
    eligible = int((await db.execute(
        select(func.count()).select_from(LiteratureIngestRun).where(*predicate)
    )).scalar_one() or 0)
    updated = 0
    if apply and eligible:
        # Re-evaluate every predicate under row locks. A run changed or bound
        # after the inspection query is excluded instead of being overwritten.
        runs = (await db.execute(
            select(LiteratureIngestRun)
            .where(*predicate)
            .with_for_update(skip_locked=True)
        )).scalars().all()
        for run in runs:
            checkpoint = dict(run.checkpoint or {})
            checkpoint["recovery"] = {
                "reason_code": "stale_unbound_ingest_run",
                "reconciled_at": observed_at.isoformat(),
            }
            run.checkpoint = checkpoint
            run.status = "failed"
            run.completed_at = observed_at
            run.error = "stale_unbound_ingest_run"
            updated += 1
        await db.commit()
    return {
        "schema_version": 1,
        "mode": "apply" if apply else "dry_run",
        "stale_after_minutes": stale_after_minutes,
        "eligible_count": eligible,
        "updated_count": updated,
        "not_updated_count": max(0, eligible - updated) if apply else eligible,
    }


__all__ = ["reconcile_stale_unbound_runs"]
