#!/usr/bin/env python3
"""Bootstrap Mapping Registry v3 from every current source and observation."""

from __future__ import annotations

import argparse
import asyncio
import json

from src.core import get_database
from src.services.disease_mapping_registry_service import disease_mapping_registry_service


async def run(*, apply: bool) -> dict:
    async with get_database() as db:
        result = await disease_mapping_registry_service.bootstrap_all_sources(db)
        if apply:
            await db.commit()
            result["mode"] = "applied"
        else:
            await db.rollback()
            result["mode"] = "rehearsed_rollback"
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rehearse", action="store_true", help="Build and validate, then roll back")
    mode.add_argument("--apply", action="store_true", help="Persist the complete v3 registry bootstrap")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
