"""Cross-command serialization for disease fact and ontology mutations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


DISEASE_DATA_MUTATION_LOCK_KEY = "globalid:disease_data_mutation:v1"


async def acquire_disease_data_mutation_lock(db: Any) -> None:
    """Acquire the shared transaction-scoped disease mutation mutex.

    Live dual writes, source-series backfills, ontology migrations, and
    restores can touch overlapping natural keys. They must take this lock
    before their first disease-table write so PostgreSQL serializes those
    transactions instead of relying on incidental row-lock order.
    """

    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": DISEASE_DATA_MUTATION_LOCK_KEY},
    )
