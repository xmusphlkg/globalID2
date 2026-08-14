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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the versioned Research Radar autopilot policy")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate decisions without writing them")
    parser.add_argument("--no-export", action="store_true", help="Do not refresh public Research Radar artifacts")
    args = parser.parse_args()
    print(json.dumps(
        asyncio.run(run(dry_run=args.dry_run, export=not args.no_export)),
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
