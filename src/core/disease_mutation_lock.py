"""Transaction locks for independent country writes and exclusive maintenance."""

from __future__ import annotations

import re
import time
from typing import Any

from loguru import logger
from sqlalchemy import text


DISEASE_DATA_MUTATION_LOCK_KEY = "globalid:disease_data_mutation:v1"
COUNTRY_DATA_MUTATION_LOCK_NAMESPACE = "globalid:country_data_mutation:v1"


def country_mutation_scope(country_code: str) -> str:
    """National feeds and all their subdivisions share one write partition."""
    code = str(country_code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}(?:-[A-Z0-9]+)*", code):
        raise ValueError(f"Invalid disease mutation country scope: {country_code!r}")
    return code.split("-", 1)[0]


async def acquire_disease_data_mutation_lock(db: Any) -> None:
    """Exclude every fact writer for cross-country maintenance.

    Source-series backfills, ontology migrations and restores retain the
    original exclusive key. They acquire it before their first disease write.
    This also excludes old workers that still use the key for live writes.
    """

    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": DISEASE_DATA_MUTATION_LOCK_KEY},
    )


async def acquire_country_data_mutation_lock(db: Any, country_code: str) -> None:
    """Serialize one country's writes while allowing other countries to commit.

    Lock order is global shared gate, then national exclusive partition. Global
    maintenance takes the same gate exclusively. Never upgrade a live shared
    gate to exclusive in the same transaction: maintenance must take its
    exclusive gate first. PostgreSQL releases both on rollback or disconnect.
    """
    scope = country_mutation_scope(country_code)
    started = time.monotonic()
    await db.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext(:lock_key))"),
        {"lock_key": DISEASE_DATA_MUTATION_LOCK_KEY},
    )
    # The two-integer advisory namespace is disjoint from the original
    # one-integer maintenance key, even if their hashes happen to coincide.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:namespace), hashtext(:country_scope))"),
        {"namespace": COUNTRY_DATA_MUTATION_LOCK_NAMESPACE, "country_scope": scope},
    )
    waited = time.monotonic() - started
    if waited >= 1:
        logger.bind(event="country_mutation_lock_wait", country_scope=scope,
                    elapsed_seconds=round(waited, 3)).warning(
            "Disease mutation lock acquired | country={} waited_seconds={:.3f}", scope, waited,
        )
