"""Persistence helpers for the latest realtime monitor report."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from src.domain import RealtimeMonitorState

MONITOR_ID = "realtime-data"


def _checked_at(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def save_monitor_report(db, report: dict) -> None:
    """Upsert one latest report without retaining an unbounded JSON archive."""
    statement = insert(RealtimeMonitorState).values(
        monitor_id=MONITOR_ID, checked_at=_checked_at(report["checked_at"]),
        status=report["status"], report=report)
    await db.execute(statement.on_conflict_do_update(
        index_elements=[RealtimeMonitorState.monitor_id],
        set_={"checked_at": statement.excluded.checked_at, "status": statement.excluded.status,
              "report": statement.excluded.report, "updated_at": func.now()},
        where=RealtimeMonitorState.checked_at < statement.excluded.checked_at))
    await db.commit()


async def load_monitor_report(db) -> dict | None:
    try:
        # An undefined table aborts the PostgreSQL transaction; keep the
        # surrounding overview reads usable during a rolling migration.
        async with db.begin_nested():
            row = (await db.execute(select(RealtimeMonitorState.report).where(
                RealtimeMonitorState.monitor_id == MONITOR_ID))).scalar_one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01" or "no such table: realtime_monitor_states" in str(exc.orig):
            return None
        raise
    return row if isinstance(row, dict) else None

