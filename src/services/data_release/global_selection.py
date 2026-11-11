"""Validate exact publication selections for global data channels."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from src.domain.collection import PublishDirty

SELECTION_BATCH_SIZE = 5000


def selection_batches(dirty_ids: list[int]):
    """Keep PostgreSQL bind counts bounded for a large review backlog."""
    ordered = sorted(dirty_ids)
    for offset in range(0, len(ordered), SELECTION_BATCH_SIZE):
        yield ordered[offset:offset + SELECTION_BATCH_SIZE]


async def validate_global_selection(db, dirty_ids: list[int], *, line: str,
                                    task_uuid: str | None = None) -> None:
    """Reject cross-channel, stale, missing or partially acknowledged selections.

    A task may retry its own completed selection after its public pointer has
    moved but finalization failed. A different task may never reuse those IDs.
    New changes are deliberately excluded from a frozen candidate.
    """
    if line not in {"situation", "literature"}:
        raise ValueError("Unknown global publication channel")
    label = line.capitalize()
    if (not dirty_ids or any(type(value) is not int or value <= 0 for value in dirty_ids)
            or len(set(dirty_ids)) != len(dirty_ids)):
        raise ValueError(f"{label} JSON requires exact positive dirty IDs")
    for batch in selection_batches(dirty_ids):
        rows = list((await db.execute(select(PublishDirty).where(
            PublishDirty.id.in_(batch)).order_by(PublishDirty.id).with_for_update())).scalars())
        if (set(row.id for row in rows) != set(batch)
                or any(row.line != line or row.scope_key != "*" for row in rows)):
            raise ValueError(f"{label} JSON selection does not match actual dirty rows")
        if task_uuid and any(row.published_at is not None and row.published_task_uuid != task_uuid
                             for row in rows):
            raise ValueError(f"Another {label} publication already consumed this selection")


async def acknowledge_global_selection(db, dirty_ids: list[int], *, line: str, task_uuid: str) -> None:
    """Acknowledge only this candidate's locked IDs in the caller's transaction."""
    if not task_uuid:
        raise ValueError("Publication acknowledgement requires a task identity")
    await validate_global_selection(db, dirty_ids, line=line, task_uuid=task_uuid)
    published_at = datetime.now(timezone.utc)
    for batch in selection_batches(dirty_ids):
        await db.execute(update(PublishDirty).where(
            PublishDirty.id.in_(batch), PublishDirty.line == line,
            PublishDirty.scope_key == "*", PublishDirty.published_at.is_(None),
        ).values(published_at=published_at, published_task_uuid=task_uuid))
