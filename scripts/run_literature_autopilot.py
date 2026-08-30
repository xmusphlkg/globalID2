#!/usr/bin/env python3
"""Evaluate or apply the Research Radar automatic publication policy."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.literature_automation_service import literature_automation_service  # noqa: E402


async def run(*, dry_run: bool, export: bool) -> dict:
    return await literature_automation_service.reconcile(dry_run=dry_run, export=export)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the versioned Research Radar autopilot policy")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Persist audited decisions; without this flag the command is read-only",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly evaluate decisions without writing them (the default)",
    )
    parser.add_argument("--no-export", action="store_true", help="Do not refresh public Research Radar artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(
        asyncio.run(run(dry_run=not args.apply, export=not args.no_export)),
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
