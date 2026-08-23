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
from src.services.situation_v3.persistence import event_labels_v3  # noqa: E402


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


def _labels_by_id(labels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(label["label_id"]): dict(label) for label in labels}


def _assign_import_splits(
    *,
    imported_labels: list[dict[str, Any]],
    existing_labels: list[dict[str, Any]],
    cadence: str,
    created_by: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assign splits across the complete label population.

    Imported labels override existing rows with the same id.  Existing labels
    are still rewritten when their split changes so the persisted population
    keeps one chronological 70/15/15 partition.
    """

    merged = _labels_by_id(existing_labels)
    imported_ids: list[str] = []
    for label in imported_labels:
        row = dict(label)
        row["created_by"] = created_by
        merged[str(row["label_id"])] = row
        imported_ids.append(str(row["label_id"]))
    splits = assign_temporal_splits(
        merged.values(),
        cadence=cadence,
        embargo_periods=2,
    )
    for label_id, label in merged.items():
        label["split"] = splits.get(label_id, "unassigned")
    ordered = sorted(
        merged.values(),
        key=lambda label: (
            label["first_official_published_at"],
            str(label["label_id"]),
        ),
    )
    return ordered, [merged[label_id] for label_id in imported_ids]


async def _persist_labels(labels: list[dict[str, Any]]) -> None:
    for label in labels:
        await upsert_event_label_v3(**label)


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
    existing_labels = await event_labels_v3() if args.apply else []
    labels_to_persist, imported_labels = _assign_import_splits(
        imported_labels=labels,
        existing_labels=existing_labels,
        cadence=args.cadence,
        created_by=args.created_by,
    )
    if args.apply:
        await _persist_labels(labels_to_persist)
    return {
        "mode": "applied" if args.apply else "dry_run",
        "input_events": len(events),
        "accepted_authoritative_labels": len(labels),
        "rejected_events": len(events) - len(labels),
        "existing_labels_rebalanced": len(existing_labels),
        "persisted_label_count": len(labels_to_persist) if args.apply else 0,
        "split_counts": {
            split: sum(label["split"] == split for label in labels_to_persist)
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
            for label in imported_labels
        ],
    }


def main() -> int:
    args = parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
