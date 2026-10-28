#!/usr/bin/env python3
"""Collect official international authority notices and write the site shards.

Runs every adapter in ``src/services/global_sources`` (WHO Disease Outbreak
News, ECDC CDTR, WHO WER and WHO WPRO surveillance), normalizes the records onto
GIDS ``disease_id`` + ISO country + date keys, and writes the sharded JSON under
``astro-site/src/data/situation/international``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.global_sources.collection import DEFAULT_SINCE_DAYS, collect
from src.services.global_sources.export_static import DEFAULT_OUTPUT_DIR, DEFAULT_RECENT_LIMIT, export_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Export international authority notices for the GIDS Situation Room")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS, help="Only keep records published inside this window")
    parser.add_argument("--don-limit", type=int, default=200, help="Maximum Disease Outbreak News items requested from the WHO hub")
    parser.add_argument("--recent-limit", type=int, default=DEFAULT_RECENT_LIMIT, help="How many recent records latest.json carries")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Destination directory for the JSON shards")
    args = parser.parse_args()

    run = collect(since_days=args.since_days, don_limit=args.don_limit)
    summary = export_events(run, output_dir=args.output_dir, recent_limit=args.recent_limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["event_count"] == 0:
        # Nothing usable survived collection: surface it as a non-zero exit so a
        # scheduled run is visible instead of silently writing an empty export.
        raise SystemExit(2)


if __name__ == "__main__":
    main()
