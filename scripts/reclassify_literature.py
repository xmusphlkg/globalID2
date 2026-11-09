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
    parser.add_argument("--doi", action="append", default=None, help="Restrict the scan to an exact DOI; repeatable")
    parser.add_argument("--after-id", type=int, default=None, help="Resume after a database ID")
    parser.add_argument("--journal", action="append", default=None, help="Restrict to an exact journal name; repeatable")
    parser.add_argument("--excluded-only", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if (args.after_id is not None or args.journal or args.excluded_only) and args.limit is None:
        parser.error("scoped historical scans require --limit")
    result = asyncio.run(reclassify_existing_literature(
        dry_run=args.dry_run, limit=args.limit, dois=args.doi, after_id=args.after_id,
        journals=args.journal, excluded_only=args.excluded_only,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))

