#!/usr/bin/env python3
"""Backfill current Research Radar classification without provider requests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.reclassification import reclassify_existing_literature  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    result = asyncio.run(reclassify_existing_literature(dry_run=args.dry_run, limit=args.limit))
    print(json.dumps(result, ensure_ascii=False, indent=2))

