#!/usr/bin/env python3
"""Reconcile or execute Research Radar evidence-gap discovery.

Examples:
    python scripts/discover_literature_gaps.py --refresh-only
    python scripts/discover_literature_gaps.py --limit 4
    python scripts/discover_literature_gaps.py --gap-id gap_abcd
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.literature_gap_service import literature_gap_service  # noqa: E402


async def run(args: argparse.Namespace) -> dict:
    if args.refresh_only:
        return await literature_gap_service.refresh_from_snapshot()
    return await literature_gap_service.execute(
        gap_ids=args.gap_id,
        limit=args.limit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover literature for active evidence gaps")
    parser.add_argument("--refresh-only", action="store_true", help="Only reconcile gaps from the latest snapshot")
    parser.add_argument("--gap-id", action="append", default=[], help="Run a specific gap; may be repeated")
    parser.add_argument("--limit", type=int, default=None, help="Maximum gaps to process")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
