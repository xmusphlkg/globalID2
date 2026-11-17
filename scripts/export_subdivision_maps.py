"""Refresh local map shards from published country snapshots and DB population.

No disease/population database writes and no deployment. Normal site exports
also run this writer automatically, including incremental country refreshes.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.database import get_db, dispose_database
from src.generation.site_subdivision_maps import fetch_map_populations, write_subdivision_maps


async def run(args):
    try:
        async with get_db() as session:
            population = await fetch_map_populations(session)
        index = write_subdivision_maps(args.snapshot_root, args.public_root, population)
        print("Map countries:", ", ".join(row["code"] for row in index["countries"]))
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=ROOT / "astro-site/src/data")
    parser.add_argument("--public-root", type=Path, default=ROOT / "astro-site/public/site-data")
    asyncio.run(run(parser.parse_args()))
