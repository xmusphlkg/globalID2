#!/usr/bin/env python3
"""Repair short abstracts on eligible review articles; dry-run by default."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.literature.abstract_repair import repair_abstracts
from src.literature.maintenance_cli import run_maintenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--retry-hours", type=int, default=168)
    args = parser.parse_args()
    if args.limit < 1 or args.retry_hours < 1:
        parser.error("limit and retry-hours must be positive")
    print(
        json.dumps(
            run_maintenance(
                lambda: repair_abstracts(
                    limit=args.limit, retry_hours=args.retry_hours, apply=args.apply
                )
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
