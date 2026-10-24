"""Bounded reconciliation for stale Research Radar ingest runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from src.domain import LiteratureIngestRun, Task, TaskStatus


_TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
)


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


def _stale_run_result(
    *,
    apply: bool,
    stale_after_minutes: float,
    eligible: int,
    updated: int,
) -> dict[str, int | float | str | bool]:
    return {
        "schema_version": 1,
        "mode": "apply" if apply else "dry_run",
        "stale_after_minutes": stale_after_minutes,
        "eligible_count": eligible,
        "updated_count": updated,
        "not_updated_count": max(0, eligible - updated) if apply else eligible,
    }


def _fail_running_run(run: LiteratureIngestRun, *, reason_code: str, observed_at: datetime) -> None:
    checkpoint = dict(run.checkpoint or {})
    checkpoint["recovery"] = {
        "reason_code": reason_code,
        "reconciled_at": observed_at.isoformat(),
    }
    run.checkpoint = checkpoint
    run.status = "failed"
    run.completed_at = observed_at
    run.error = reason_code


async def reconcile_stale_terminal_task_runs(
    db: Any,
    *,
    stale_after_minutes: float,
    apply: bool = False,
    now: datetime | None = None,
) -> dict[str, int | float | str | bool]:
    """Fail stale running rows only when their task is already terminal.

    A run whose task is still pending, queued, running, or retrying stays
    running. Unbound rows are left to ``reconcile_stale_unbound_runs``.
    """

    if stale_after_minutes < 30:
        raise ValueError("stale_after_minutes must be at least 30")
    observed_at = _utc(now or datetime.now(timezone.utc))
    cutoff = observed_at - timedelta(minutes=stale_after_minutes)
    terminal_tasks = select(Task.task_uuid).where(
        Task.status.in_(_TERMINAL_TASK_STATUSES)
    )
    predicate = (
        LiteratureIngestRun.task_uuid.is_not(None),
        LiteratureIngestRun.status == "running",
        LiteratureIngestRun.started_at <= cutoff,
        LiteratureIngestRun.task_uuid.in_(terminal_tasks),
    )
    eligible = int((await db.execute(
        select(func.count()).select_from(LiteratureIngestRun).where(*predicate)
    )).scalar_one() or 0)
    updated = 0
    if apply and eligible:
        runs = (await db.execute(
            select(LiteratureIngestRun)
            .where(*predicate)
            .with_for_update(skip_locked=True)
        )).scalars().all()
        for run in runs:
            _fail_running_run(
                run,
                reason_code="stale_terminal_task_ingest_run",
                observed_at=observed_at,
            )
            updated += 1
        await db.commit()
    return _stale_run_result(
        apply=apply,
        stale_after_minutes=stale_after_minutes,
        eligible=eligible,
        updated=updated,
    )


async def supersede_earlier_running_runs(
    db: Any,
    *,
    task_uuid: str,
    current_run_uuid: str,
    now: datetime | None = None,
) -> int:
    """Close earlier running attempts of the same task after a retry starts."""

    if not task_uuid or not current_run_uuid:
        return 0
    observed_at = _utc(now or datetime.now(timezone.utc))
    runs = (await db.execute(
        select(LiteratureIngestRun).where(
            LiteratureIngestRun.task_uuid == task_uuid,
            LiteratureIngestRun.status == "running",
            LiteratureIngestRun.run_uuid != current_run_uuid,
        )
    )).scalars().all()
    for run in runs:
        _fail_running_run(
            run,
            reason_code="superseded_by_later_attempt",
            observed_at=observed_at,
        )
    if runs:
        await db.commit()
    return len(runs)


__all__ = [
    "reconcile_stale_terminal_task_runs",
    "reconcile_stale_unbound_runs",
    "supersede_earlier_running_runs",
]
