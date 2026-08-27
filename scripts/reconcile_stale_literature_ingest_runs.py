#!/usr/bin/env python3
"""Dry-run or reconcile legacy Research Radar runs left running."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_IMPORT_LOG_SINK = io.StringIO()
with redirect_stderr(_IMPORT_LOG_SINK):
    from loguru import logger
    from src.core import dispose_database, get_database
    from src.literature.ingest_run_recovery import reconcile_stale_unbound_runs

logger.remove()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run by default; --apply terminalizes only unbound running "
            "literature ingest rows older than the cutoff."
        )
    )
    parser.add_argument("--apply", action="store_true", help="persist reconciliation")
    parser.add_argument(
        "--stale-after-minutes",
        type=float,
        default=120.0,
        help="minimum run age (default: 120; minimum: 30)",
    )
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    if args.stale_after_minutes < 30:
        print(json.dumps({
            "schema_version": 1,
            "status": "input_error",
            "error_code": "stale_cutoff_below_minimum",
        }, separators=(",", ":")))
        return 2
    try:
        async with get_database() as db:
            result = await reconcile_stale_unbound_runs(
                db,
                stale_after_minutes=args.stale_after_minutes,
                apply=args.apply,
            )
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception:
        # Do not echo database details, row identifiers, or exception text.
        print(json.dumps({
            "schema_version": 1,
            "status": "failed",
            "error_code": "ingest_run_reconciliation_failed",
        }, separators=(",", ":")))
        return 3
    finally:
        await dispose_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
