#!/usr/bin/env python3
"""Register CL, CO and MW for staged official surveillance ingestion."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.core.database import dispose_database, get_db
from src.core.db_schema import ensure_country_scope_for_code
from src.domain import Country

COUNTRIES = ("CL", "CO", "MW")
CONFIG_FIELDS = (
    "data_source_url", "data_source_type", "crawler_config", "parser_config",
    "disease_mapping_rules", "report_config", "notes",
)


async def bootstrap(*, apply: bool = False) -> dict:
    result = {"mode": "applied" if apply else "preview", "countries": []}
    async with get_db() as db:
        for code in COUNTRIES:
            existing = (await db.execute(select(Country).where(Country.code == code))).scalar_one_or_none()
            entry = {"code": code, "action": "exists" if existing else "create"}
            result["countries"].append(entry)
            if not apply:
                continue
            if existing is None:
                profile = get_country_profile(code)
                config = get_country_bootstrap_config(code)
                existing = Country(
                    code=code, name=profile.name, name_en=profile.name_en,
                    name_local=profile.name_local, language=profile.language,
                    timezone=profile.timezone,
                )
                for field in CONFIG_FIELDS:
                    if field in config:
                        setattr(existing, field, config[field])
                db.add(existing)
                await db.flush()
            await ensure_country_scope_for_code(db, code)
        if apply:
            await db.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    async def run() -> None:
        try:
            print(json.dumps(await bootstrap(apply=args.apply), ensure_ascii=False))
        finally:
            await dispose_database()

    asyncio.run(run())


if __name__ == "__main__":
    main()
