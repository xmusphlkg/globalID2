#!/usr/bin/env python3
"""Validate and optionally import authoritative Situation v3.2 event labels."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_room import load_config  # noqa: E402
from src.services.situation_v3.labels import (  # noqa: E402
    assign_temporal_splits,
    label_from_official_event,
)
from src.services.situation_v3.persistence import upsert_event_label_v3  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON event export (list or {events: [...]})")
    parser.add_argument("--cadence", choices=("weekly", "monthly"), required=True)
    parser.add_argument("--created-by", default="situation-v3-label-import")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist validated labels; default is a read-only preview.",
    )
    return parser.parse_args()


def _read_events(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ValueError("input must be a JSON event list or an object with an events list")
    return values


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config()
    domains = (
        config.get("publication", {})
        .get("official_evidence", {})
        .get("authoritative_domains", [])
    )
    events = _read_events(args.input)
    labels = [
        label
        for event in events
        if (label := label_from_official_event(event, allowed_domains=domains))
        is not None
    ]
    splits = assign_temporal_splits(labels, cadence=args.cadence, embargo_periods=2)
    for label in labels:
        label["split"] = splits[label["label_id"]]
        label["created_by"] = args.created_by
    if args.apply:
        for label in labels:
            await upsert_event_label_v3(**label)
    return {
        "mode": "applied" if args.apply else "dry_run",
        "input_events": len(events),
        "accepted_authoritative_labels": len(labels),
        "rejected_events": len(events) - len(labels),
        "split_counts": {
            split: sum(label["split"] == split for label in labels)
            for split in ("development", "tuning", "locked_test", "unassigned")
        },
        "labels": [
            {
                **label,
                "event_started_at": (
                    label["event_started_at"].isoformat()
                    if label["event_started_at"]
                    else None
                ),
                "first_official_published_at": label[
                    "first_official_published_at"
                ].isoformat(),
            }
            for label in labels
        ],
    }


def main() -> int:
    args = parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
