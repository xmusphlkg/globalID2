#!/usr/bin/env python3
"""Backfill current Research Radar classification without provider requests."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.maintenance_cli import (
    exclusive_apply_lock,
    run_maintenance,
)
from src.literature.reclassification import reclassify_existing_literature

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--doi", action="append", default=None, help="Restrict the scan to an exact DOI; repeatable")
    parser.add_argument("--after-id", type=int, default=None, help="Resume after a database ID")
    parser.add_argument("--journal", action="append", default=None, help="Restrict to an exact journal name; repeatable")
    statuses = parser.add_mutually_exclusive_group()
    statuses.add_argument("--excluded-only", action="store_true")
    statuses.add_argument("--published-only", action="store_true", help="Only refresh records eligible for public export")
    parser.add_argument("--stale-only", action="store_true", help="Skip records already classified by the current rules")
    parser.add_argument("--workers", type=int, default=1, help="Pure-classification processes (1–16); DB writes remain serial")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be from 1 to 16")
    if args.workers > 1 and args.limit is None:
        parser.error("parallel classification requires --limit")
    if (args.after_id is not None or args.journal or args.excluded_only or args.published_only or args.stale_only) and args.limit is None:
        parser.error("scoped historical scans require --limit")
    lock = (nullcontext() if args.dry_run else exclusive_apply_lock(
        ROOT / "data/cache/literature_reclassification.lock", operation="literature reclassification",
    ))
    pool = (nullcontext(None) if args.workers == 1 else
            ProcessPoolExecutor(max_workers=args.workers, mp_context=get_context("spawn")))
    with lock, pool as executor:
        result = run_maintenance(lambda: reclassify_existing_literature(
            dry_run=args.dry_run, limit=args.limit, dois=args.doi, after_id=args.after_id,
            journals=args.journal, excluded_only=args.excluded_only,
            published_only=args.published_only, stale_only=args.stale_only,
            executor=executor,
        ))
    print(json.dumps(result, ensure_ascii=False, indent=2))

