"""Transactional helpers used by the database rebuild importers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BatchInsertResult:
    attempted: int
    inserted: int
    failed: int
    batch_error: str | None = None


async def insert_with_savepoint_fallback(
    db,
    statement,
    rows: Sequence[Mapping[str, object]],
) -> BatchInsertResult:
    """Insert a batch, isolating both batch and row failures with savepoints.

    A session-wide rollback here would discard successful work performed since
    the previous stage checkpoint.  Nested transactions keep the outer stage
    transaction intact while still allowing invalid rows to be skipped.
    """
    if not rows:
        return BatchInsertResult(attempted=0, inserted=0, failed=0)

    try:
        async with db.begin_nested():
            await db.execute(statement, rows)
    except Exception as batch_error:
        inserted = 0
        for row in rows:
            try:
                async with db.begin_nested():
                    await db.execute(statement, row)
            except Exception:
                continue
            inserted += 1
        return BatchInsertResult(
            attempted=len(rows),
            inserted=inserted,
            failed=len(rows) - inserted,
            batch_error=str(batch_error)[:500],
        )

    return BatchInsertResult(attempted=len(rows), inserted=len(rows), failed=0)
