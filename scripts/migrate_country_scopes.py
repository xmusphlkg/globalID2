#!/usr/bin/env python3
"""Migrate country/scope structure to canonical countries + country_scopes."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from src.core.database import get_session_maker
from src.core.db_schema import ensure_country_scope_schema


async def main() -> None:
    SessionMaker = get_session_maker()
    async with SessionMaker() as db:
        await ensure_country_scope_schema(db)
        await db.commit()

        canonical = await db.execute(text("""
            SELECT COUNT(*)
            FROM countries
            WHERE code ~ '^[A-Z]{2}$'
        """))
        scopes = await db.execute(text("SELECT COUNT(*) FROM country_scopes"))
        mapping_scopes = await db.execute(text("SELECT COUNT(DISTINCT country_code) FROM disease_mappings"))

        print("✓ Country scope migration completed")
        print(f"  Canonical countries : {canonical.scalar()}")
        print(f"  Country scopes      : {scopes.scalar()}")
        print(f"  Mapping scope codes : {mapping_scopes.scalar()}")


if __name__ == "__main__":
    asyncio.run(main())
