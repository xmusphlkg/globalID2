"""Validate exact publication selections for global data channels."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, any_, bindparam, select, update
from sqlalchemy.dialects.postgresql import ARRAY

from src.domain.collection import PublishDirty

SELECTION_BATCH_SIZE = 5000


def selection_batches(dirty_ids: list[int]):
    """Keep PostgreSQL bind counts bounded for a large review backlog."""
    if (any(type(value) is not int or value <= 0 for value in dirty_ids)
            or len(set(dirty_ids)) != len(dirty_ids)):
        raise ValueError("Publication selection requires unique positive dirty IDs")
    ordered = sorted(dirty_ids)
    for offset in range(0, len(ordered), SELECTION_BATCH_SIZE):
        yield ordered[offset:offset + SELECTION_BATCH_SIZE]


def selected_id_predicate(db, batch: list[int]):
    """Use the PK without a competing pending/channel index access path.

    A generic PostgreSQL plan may scan the entire pending line and test a
    5,000-element array for every row when channel/pending predicates are in
    SQL. Callers must validate those fields on locked rows before updating.
    """
    # Unit tests and lightweight callers may provide a database double that
    # only implements ``execute``.  Real SQLAlchemy sessions expose
    # ``get_bind``; keep the portable ``IN`` predicate when that capability is
    # unavailable rather than requiring every test double to model the whole
    # session API.
    get_bind = getattr(db, "get_bind", None)
    bind = get_bind() if callable(get_bind) else None
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect == "postgresql":
        return PublishDirty.id == any_(bindparam(
            "selected_dirty_ids", value=batch, type_=ARRAY(BigInteger), unique=True))
    return PublishDirty.id.in_(batch)


async def read_selected_batch(db, batch: list[int], *, lock: bool = False):
    statement = select(PublishDirty).where(selected_id_predicate(db, batch)).order_by(PublishDirty.id)
    if lock:
        statement = statement.with_for_update()
    # An identity-map entry may predate a competing publisher's commit.
    # ``ScalarResult.all`` works for SQLAlchemy sessions and keeps lightweight
    # test doubles compatible with the result API they expose.
    return (await db.execute(statement.execution_options(populate_existing=True))).scalars().all()


async def acknowledge_locked_batch(db, ids: list[int], *, published_at, task_uuid: str):
    """Update only IDs already checked under FOR UPDATE in this transaction."""
    if ids:
        await db.execute(update(PublishDirty).where(selected_id_predicate(db, ids)).values(
            published_at=published_at, published_task_uuid=task_uuid))


async def validate_global_selection(db, dirty_ids: list[int], *, line: str,
                                    task_uuid: str | None = None) -> list:
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
    selected = []
    for batch in selection_batches(dirty_ids):
        rows = await read_selected_batch(db, batch, lock=True)
        if ({row.id for row in rows} != set(batch)
                or any(row.line != line or row.scope_key != "*" for row in rows)):
            raise ValueError(f"{label} JSON selection does not match actual dirty rows")
        if task_uuid and any(row.published_at is not None and row.published_task_uuid != task_uuid
                             for row in rows):
            raise ValueError(f"Another {label} publication already consumed this selection")
        selected.extend(rows)
    return selected


async def acknowledge_global_selection(db, dirty_ids: list[int], *, line: str, task_uuid: str) -> None:
    """Acknowledge only this candidate's locked IDs in the caller's transaction."""
    if not task_uuid:
        raise ValueError("Publication acknowledgement requires a task identity")
    rows = await validate_global_selection(db, dirty_ids, line=line, task_uuid=task_uuid)
    published_at = datetime.now(timezone.utc)
    for batch in selection_batches([row.id for row in rows if row.published_at is None]):
        await acknowledge_locked_batch(db, batch, published_at=published_at, task_uuid=task_uuid)


async def acknowledge_content_selection(db, dirty_ids: list[int], *, task_uuid: str,
                                       situation_detached: bool, literature_detached: bool) -> None:
    """Consume exact static-content markers without advancing detached channels.

    Every batch shares the caller's transaction; a later failure must roll back
    earlier acknowledgements. Surveillance is always owned by its JSON publisher.
    """
    if not task_uuid:
        raise ValueError("Publication acknowledgement requires a task identity")
    excluded_lines = ["surveillance"]
    if situation_detached:
        excluded_lines.append("situation")
    if literature_detached:
        excluded_lines.append("literature")
    published_at = datetime.now(timezone.utc)
    for batch in selection_batches(dirty_ids):
        rows = await read_selected_batch(db, batch, lock=True)
        if {row.id for row in rows} != set(batch):
            raise ValueError("Content publication selection has missing dirty rows")
        selected = [row for row in rows if row.line not in excluded_lines]
        if any(row.published_at is not None and row.published_task_uuid != task_uuid for row in selected):
            raise ValueError("Another publication already consumed this content selection")
        await acknowledge_locked_batch(db, [row.id for row in selected if row.published_at is None],
                                       published_at=published_at, task_uuid=task_uuid)
