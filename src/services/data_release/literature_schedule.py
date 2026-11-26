"""One publication cadence shared by the scheduler and its monitor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def literature_publication_due_at(*, created_at: datetime | None,
                                  completed_at: datetime | None,
                                  status, interval_minutes: int) -> datetime | None:
    """Wait one interval after a terminal task finishes, including failures.

    An active task owns the reservation rather than a new scheduled deadline.
    Legacy terminal rows without completion timestamps fall back to creation.
    """
    if getattr(status, "value", status) not in {"completed", "failed", "cancelled"}:
        return None
    last = completed_at or created_at
    if last is None:
        return None
    last = last.replace(tzinfo=last.tzinfo or timezone.utc).astimezone(timezone.utc)
    return last + timedelta(minutes=interval_minutes)
