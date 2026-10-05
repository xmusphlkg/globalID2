#!/usr/bin/env python3
"""Compact legacy Research Radar source payloads in bounded transactions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.maintenance_cli import run_maintenance  # noqa: E402
from src.literature.storage_maintenance import (
    compact_source_payload_batch,  # noqa: E402
)


async def run(*, apply: bool, batch_size: int, max_rows: int | None) -> dict:
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    totals = {
        "scanned": 0,
        "compacted": 0,
        "before_bytes": 0,
        "after_bytes": 0,
        "saved_bytes": 0,
        "batches": 0,
    }
    while max_rows is None or totals["scanned"] < max_rows:
        remaining = batch_size if max_rows is None else min(batch_size, max_rows - totals["scanned"])
        if remaining <= 0:
            break
        result = await compact_source_payload_batch(
            batch_size=remaining,
            dry_run=not apply,
        )
        totals["batches"] += 1
        for key in ("scanned", "compacted", "before_bytes", "after_bytes", "saved_bytes"):
            totals[key] += int(result[key])
        if not apply or result["scanned"] < remaining:
            break
    totals["dry_run"] = not apply
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist compaction; default is one read-only batch")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-rows", type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 10_000:
        parser.error("--batch-size must be between 1 and 10000")
    if args.max_rows is not None and args.max_rows < 1:
        parser.error("--max-rows must be at least 1")
    print(json.dumps(
        run_maintenance(lambda: run(apply=args.apply, batch_size=args.batch_size, max_rows=args.max_rows)),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
