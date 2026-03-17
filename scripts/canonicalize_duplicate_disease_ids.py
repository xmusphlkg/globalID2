#!/usr/bin/env python3
"""Canonicalize duplicate disease IDs in the database.

This script converges historically duplicated standard disease concepts onto a
single canonical disease_id so country mappings and existing disease_records use
the same global identifiers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db
from src.core.logging import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class CanonicalPair:
    old_id: str
    new_id: str
    reason: str


CANONICAL_PAIRS = [
    CanonicalPair("D111", "D016", "Novel influenza A unified onto D016"),
    CanonicalPair("D022", "D110", "Meningococcal concepts unified onto D110"),
    CanonicalPair("D027", "D110", "Meningococcal concepts unified onto D110"),
    CanonicalPair("D130", "D051", "Zika concepts unified onto D051"),
]


async def _get_numeric_disease_id(db, disease_code: str):
    result = await db.execute(
        text("SELECT id FROM diseases WHERE name = :code"),
        {"code": disease_code},
    )
    row = result.fetchone()
    return row[0] if row else None


async def _log_counts(db, label: str) -> None:
    logger.info("=== {} ===", label)
    for pair in CANONICAL_PAIRS:
        result = await db.execute(text("""
            SELECT COUNT(*)
            FROM disease_mappings
            WHERE disease_id = :disease_id
        """), {"disease_id": pair.old_id})
        old_mappings = result.scalar() or 0

        result = await db.execute(text("""
            SELECT COUNT(*)
            FROM disease_mappings
            WHERE disease_id = :disease_id
        """), {"disease_id": pair.new_id})
        new_mappings = result.scalar() or 0

        old_num = await _get_numeric_disease_id(db, pair.old_id)
        new_num = await _get_numeric_disease_id(db, pair.new_id)

        old_records = 0
        new_records = 0
        if old_num is not None:
            result = await db.execute(text("SELECT COUNT(*) FROM disease_records WHERE disease_id = :id"), {"id": old_num})
            old_records = result.scalar() or 0
        if new_num is not None:
            result = await db.execute(text("SELECT COUNT(*) FROM disease_records WHERE disease_id = :id"), {"id": new_num})
            new_records = result.scalar() or 0

        logger.info(
            "{} -> {} | mappings old/new: {}/{} | records old/new: {}/{}",
            pair.old_id,
            pair.new_id,
            old_mappings,
            new_mappings,
            old_records,
            new_records,
        )


async def canonicalize() -> None:
    async with get_db() as db:
        await _log_counts(db, "Before canonicalization")

        for pair in CANONICAL_PAIRS:
            logger.info("Canonicalizing {} -> {} ({})", pair.old_id, pair.new_id, pair.reason)

            await db.execute(text("""
                DELETE FROM disease_mappings old_map
                USING disease_mappings new_map
                WHERE old_map.disease_id = :old_id
                  AND new_map.disease_id = :new_id
                  AND old_map.country_code = new_map.country_code
                  AND old_map.local_name = new_map.local_name
            """), {"old_id": pair.old_id, "new_id": pair.new_id})

            await db.execute(text("""
                UPDATE disease_mappings
                SET disease_id = :new_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE disease_id = :old_id
            """), {"old_id": pair.old_id, "new_id": pair.new_id})

            await db.execute(text("""
                UPDATE disease_learning_suggestions
                SET suggested_disease_id = :new_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE suggested_disease_id = :old_id
            """), {"old_id": pair.old_id, "new_id": pair.new_id})

            await db.execute(text("""
                UPDATE disease_learning_suggestions
                SET final_disease_id = :new_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE final_disease_id = :old_id
            """), {"old_id": pair.old_id, "new_id": pair.new_id})

            old_num = await _get_numeric_disease_id(db, pair.old_id)
            new_num = await _get_numeric_disease_id(db, pair.new_id)
            if old_num is None or new_num is None:
                logger.warning("Skipping disease_records migration for {} -> {} because one side is missing", pair.old_id, pair.new_id)
                continue

            await db.execute(text("""
                DELETE FROM disease_records old_rec
                USING disease_records new_rec
                WHERE old_rec.disease_id = :old_num
                  AND new_rec.disease_id = :new_num
                  AND old_rec.country_id = new_rec.country_id
                  AND old_rec.time = new_rec.time
            """), {"old_num": old_num, "new_num": new_num})

            await db.execute(text("""
                UPDATE disease_records
                SET disease_id = :new_num
                WHERE disease_id = :old_num
            """), {"old_num": old_num, "new_num": new_num})

        await db.commit()
        await _log_counts(db, "After canonicalization")


if __name__ == "__main__":
    asyncio.run(canonicalize())