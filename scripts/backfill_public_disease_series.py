"""Queue an incremental site-release for stored, unpublished disease series.

Usage:
    venv/bin/python scripts/backfill_public_disease_series.py

This command queues EXPORT_DATA using committed dirty scopes. --full explicitly
requests a complete regeneration. The worker performs the same
preflight, export, build, deploy, and production verification as the publishing
page, then commits public curves and acknowledges dirty markers. It never
rewrites the public projection directly. A worker must be running to execute
the queued release; a queued response does not mean publication has completed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def enqueue_release(*, full: bool = False) -> dict:
    from src.core.database import dispose_database
    from src.services.data_release_service import data_release_service

    try:
        return await data_release_service.trigger_job(
            "site-release", manual=True, trigger="public-series-backfill", full=full
        )
    finally:
        await dispose_database()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Explicitly rebuild all countries instead of using dirty scopes",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            asyncio.run(enqueue_release(full=args.full)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
