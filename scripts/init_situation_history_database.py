#!/usr/bin/env python3
"""Create, initialize, and optionally backfill the Situation history DB."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.situation_history_database import (  # noqa: E402
    history_database_descriptor,
    init_history_database,
)
from src.services.situation_history_service import history_health, sync_history  # noqa: E402


async def _main(args: argparse.Namespace) -> None:
    created = await init_history_database(create_database=True)
    result: dict[str, object] = {
        "database": history_database_descriptor(),
        "database_created": created,
        "schema_initialized": True,
    }
    if args.backfill:
        result["backfill"] = await sync_history(mode="bootstrap")
    result["health"] = await history_health()
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently initialize the dedicated Situation Room history database"
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Reconcile every existing primary Situation snapshot into history",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
