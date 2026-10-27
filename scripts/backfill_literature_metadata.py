#!/usr/bin/env python3
"""Audit or apply a resumable OpenAlex/Unpaywall backfill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.literature.maintenance_cli import (  # noqa: E402
    ConcurrentApplyError,
    exclusive_apply_lock,
    run_maintenance,
)
from src.literature.metadata_backfill import (  # noqa: E402
    DEFAULT_CHECKPOINT_PATH,
    SUPPORTED_PROVIDERS,
    backfill_existing_literature_metadata,
)

APPLY_LOCK_PATH = ROOT / "data/cache/literature_metadata_backfill.lock"


def _exclusive_apply_lock(path: Path = APPLY_LOCK_PATH):
    return exclusive_apply_lock(path, operation="literature metadata backfill")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill bounded OpenAlex/Unpaywall metadata for DOI-bearing Research Radar records. "
            "The default is dry-run; pass --apply to write."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Persist metadata changes and checkpoint progress")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum records examined in this invocation (default: 500; repeat to continue)",
    )
    parser.add_argument(
        "--providers",
        default=",".join(SUPPORTED_PROVIDERS),
        help="Comma-separated provider subset: openalex,unpaywall",
    )
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--min-interval-seconds", type=float, default=None)
    parser.add_argument(
        "--openalex-target",
        type=float,
        default=None,
        help="Stop when DOI coverage reaches this ratio (defaults to configured target)",
    )
    parser.add_argument(
        "--unpaywall-target",
        type=float,
        default=None,
        help="Stop when DOI coverage reaches this ratio (defaults to configured target)",
    )
    parser.add_argument("--checkpoint-file", type=Path, default=DEFAULT_CHECKPOINT_PATH)
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore an existing checkpoint without deleting it (apply mode only)",
    )
    return parser


async def _main(args: argparse.Namespace) -> dict:
    providers = tuple(value.strip().lower() for value in args.providers.split(",") if value.strip())
    coverage_targets = {
        provider: target
        for provider, target in (
            ("openalex", args.openalex_target),
            ("unpaywall", args.unpaywall_target),
        )
        if target is not None
    }
    return await backfill_existing_literature_metadata(
        apply=args.apply,
        batch_size=args.batch_size,
        limit=args.limit,
        providers=providers,
        checkpoint_path=args.checkpoint_file,
        resume=not args.no_resume,
        concurrency=args.concurrency,
        min_interval_seconds=args.min_interval_seconds,
        coverage_targets=coverage_targets,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.batch_size < 1 or arguments.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be at least 1")
    providers = {value.strip().lower() for value in arguments.providers.split(",") if value.strip()}
    if not providers or not providers.issubset(SUPPORTED_PROVIDERS):
        parser.error(f"--providers must be selected from {', '.join(SUPPORTED_PROVIDERS)}")
    if arguments.concurrency is not None and not 1 <= arguments.concurrency <= 12:
        parser.error("--concurrency must be between 1 and 12")
    if arguments.min_interval_seconds is not None and not 0 <= arguments.min_interval_seconds <= 10:
        parser.error("--min-interval-seconds must be between 0 and 10")
    for flag, value in (
        ("--openalex-target", arguments.openalex_target),
        ("--unpaywall-target", arguments.unpaywall_target),
    ):
        if value is not None and not 0.0 <= value <= 1.0:
            parser.error(f"{flag} must be between 0 and 1")
    try:
        if arguments.apply:
            with _exclusive_apply_lock():
                result = run_maintenance(lambda: _main(arguments))
        else:
            result = run_maintenance(lambda: _main(arguments))
    except ConcurrentApplyError as exc:
        parser.exit(2, f"metadata backfill refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
