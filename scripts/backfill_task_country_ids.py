"""Backfill tasks.country_id from input_data.country/country_code for legacy tasks."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_session_maker
from src.domain.country import Country
from src.domain.task import Task


async def main(apply_changes: bool) -> None:
    session_maker = get_session_maker()
    async with session_maker() as db:
        rows = (
            await db.execute(
                select(Task).where(Task.country_id.is_(None)).order_by(Task.created_at.desc())
            )
        ).scalars().all()

        country_rows = (await db.execute(select(Country.id, Country.code))).all()
        country_by_code = {code.upper(): country_id for country_id, code in country_rows if code}

        updated = 0
        skipped = 0
        for task in rows:
            payload = task.input_data or {}
            if not isinstance(payload, dict):
                skipped += 1
                continue

            raw_country = payload.get("country_code") or payload.get("country")
            if raw_country is None:
                skipped += 1
                continue

            country_id = country_by_code.get(str(raw_country).strip().upper())
            if country_id is None:
                skipped += 1
                continue

            task.country_id = country_id
            updated += 1

        if apply_changes:
            await db.commit()
        else:
            await db.rollback()

    mode = "Applied" if apply_changes else "Dry run"
    print(f"{mode}: updated={updated}, skipped={skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Persist changes to the database")
    args = parser.parse_args()
    asyncio.run(main(apply_changes=args.apply))
