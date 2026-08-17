#!/usr/bin/env python3
"""Audit or apply a resumable OpenAlex/Unpaywall backfill."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.literature.metadata_backfill import (  # noqa: E402
    DEFAULT_CHECKPOINT_PATH,
    SUPPORTED_PROVIDERS,
    backfill_existing_literature_metadata,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill bounded OpenAlex/Unpaywall metadata for DOI-bearing Research Radar records. "
            "The default is dry-run; pass --apply to write."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Persist metadata changes and checkpoint progress")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="Maximum records examined in this invocation")
    parser.add_argument(
        "--providers",
        default=",".join(SUPPORTED_PROVIDERS),
        help="Comma-separated provider subset: openalex,unpaywall",
    )
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--min-interval-seconds", type=float, default=None)
    parser.add_argument("--checkpoint-file", type=Path, default=DEFAULT_CHECKPOINT_PATH)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore an existing checkpoint without deleting it (apply mode only)",
    )
    return parser


async def _main(args: argparse.Namespace) -> dict:
    providers = tuple(value.strip().lower() for value in args.providers.split(",") if value.strip())
    return await backfill_existing_literature_metadata(
        apply=args.apply,
        batch_size=args.batch_size,
        limit=args.limit,
        providers=providers,
        checkpoint_path=args.checkpoint_file,
        resume=not args.no_resume,
        concurrency=args.concurrency,
        min_interval_seconds=args.min_interval_seconds,
    )


if __name__ == "__main__":
    parser = _parser()
    arguments = parser.parse_args()
    if arguments.batch_size < 1 or arguments.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be at least 1")
    result = asyncio.run(_main(arguments))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failure_count"]:
        raise SystemExit(2)
