"""
Migration script: Add revision_count column to report_section_runs table.

Safe to run on existing databases - uses ALTER TABLE IF NOT EXISTS pattern.
Already-present column is silently skipped.

Usage:
    python scripts/dev/migrate_add_revision_count.py
"""
import asyncio
import os
import sys

# Allow running as a script from any working directory.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from sqlalchemy import text
from src.core.database import get_db


async def main() -> None:
    print("Running migration: add revision_count to report_section_runs ...")
    async with get_db() as session:
        # PostgreSQL: add column only if it doesn't already exist
        sql = text("""
            ALTER TABLE report_section_runs
            ADD COLUMN IF NOT EXISTS revision_count INTEGER NOT NULL DEFAULT 0;
        """)
        await session.execute(sql)
        await session.commit()
    print("Done. Column 'revision_count' is now present in report_section_runs.")


if __name__ == "__main__":
    asyncio.run(main())
