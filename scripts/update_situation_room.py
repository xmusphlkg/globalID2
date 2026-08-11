#!/usr/bin/env python3
"""Refresh external event metadata and deterministic Situation Room snapshots."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_room import refresh_situation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the GIDS Situation Room")
    parser.add_argument("--no-fetch-events", action="store_true", help="Recalculate signals without network access")
    args = parser.parse_args()
    payload = asyncio.run(refresh_situation(fetch_events=not args.no_fetch_events))
    print(json.dumps({key: payload.get(key) for key in ("snapshot_id", "generated_at", "data_through", "iso_week")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
