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

from src.services.situation_v3.pipeline import refresh_situation_v3  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the GIDS Situation Room")
    parser.add_argument("--no-fetch-events", action="store_true", help="Recalculate signals without network access")
    args = parser.parse_args()
    result = asyncio.run(refresh_situation_v3(fetch_events=not args.no_fetch_events))
    payload = result["report"]
    print(
        json.dumps(
            {
                "schema_version": payload.get("schema_version"),
                "report_id": (payload.get("report") or {}).get("report_id"),
                "as_of": (payload.get("report") or {}).get("as_of"),
                "revision": (payload.get("report") or {}).get("revision"),
                "quality_gate_status": (payload.get("quality_gate") or {}).get("status"),
                "run_id": result.get("run_id"),
                "published_changed": result.get("published_changed"),
                "timings": result.get("timings"),
            },
            ensure_ascii=False,
        )
    )
    if not (payload.get("quality_gate") or {}).get("passed"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
