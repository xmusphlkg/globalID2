#!/usr/bin/env python3
"""Backfill public_disease_series from the current observation ledger.

The load is resumable. A jurisdiction already published at the current
projection version is skipped unless --force is set.

Examples:
    venv/bin/python scripts/backfill_public_disease_series.py
    venv/bin/python scripts/backfill_public_disease_series.py --country JP
    venv/bin/python scripts/backfill_public_disease_series.py --jobs 2 --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.public_series_store import refresh_public_disease_series  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--country",
        action="append",
        default=[],
        help="Parent or subdivision code to refresh. Repeat for more than one. Default: every public country.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=2,
        help="How many jurisdictions to project at once (default: 2).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild jurisdictions that are already at the current projection version.",
    )
    args = parser.parse_args()
    summary = asyncio.run(
        refresh_public_disease_series(
            args.country or None,
            jobs=args.jobs,
            force=args.force,
        )
    )
    print(
        "Public series backfill: "
        f"refreshed={summary['refreshed']} skipped={summary['skipped']} "
        f"failed={len(summary['failed'])} series_rows={summary['series_rows']} "
        f"elapsed={summary['elapsed_seconds']}s"
    )
    for item in summary["failed"]:
        print(f"FAILED {item['country_code']}: {item.get('error')}", file=sys.stderr)
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
