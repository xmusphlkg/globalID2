#!/usr/bin/env python3
"""Backfill lossless disease source-series observations from a history CSV.

The command is a dry run unless ``--apply`` is supplied.  It never deletes or
aggregates records; ambiguous or conflicting source identities fail closed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.data.storage import SeriesObservationStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill ontology series facts from a normalized history CSV."
    )
    parser.add_argument("--country", required=True, help="Two-letter country code.")
    parser.add_argument(
        "--input",
        type=Path,
        help="History CSV; defaults to data/history/<country>/history_merged.csv.",
    )
    parser.add_argument(
        "--source-id",
        help="Optional ontology source ID when a country has multiple sources.",
    )
    parser.add_argument(
        "--geography-key",
        help=(
            "Optional explicit fact geography, for example "
            "country:CN:national."
        ),
    )
    parser.add_argument(
        "--value-field",
        default="Cases",
        help=(
            "CSV column containing the observation value; defaults to Cases. "
            "Quote names containing spaces, for example 'Current week'."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upsert matched rows. Without this flag the command only reports.",
    )
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


async def run(args: argparse.Namespace) -> dict[str, object]:
    country_code = args.country.strip().upper()
    if len(country_code) != 2:
        raise ValueError("--country must be a two-letter code")
    value_field = str(getattr(args, "value_field", "Cases") or "").strip()
    if not value_field:
        raise ValueError("--value-field must not be empty")
    input_path = args.input or (
        ROOT / "data" / "history" / country_code.lower() / "history_merged.csv"
    )
    rows = load_rows(input_path)
    store = SeriesObservationStore()
    built = store.build_observations(
        rows,
        country_code,
        source_id=args.source_id,
        value_field=value_field,
        geography_key=args.geography_key,
    )
    summary: dict[str, object] = {
        "mode": "apply" if args.apply else "dry_run",
        "country_code": country_code,
        "source_id": args.source_id,
        "geography_key": args.geography_key,
        "value_field": value_field,
        "input": str(input_path),
        "input_rows": len(rows),
        "matched_observations": len(built.observations),
        "matched_by_series": dict(
            sorted(Counter(row["series_code"] for row in built.observations).items())
        ),
        "skipped_unmatched": built.skipped_unmatched,
        "skipped_ambiguous": built.skipped_ambiguous,
        "skipped_invalid": built.skipped_invalid,
    }
    if args.apply:
        async with get_db() as db:
            saved = await store.save_rows(
                db,
                rows,
                country_code,
                source_id=args.source_id,
                value_field=value_field,
                geography_key=args.geography_key,
            )
        summary["saved"] = {
            "upserted": saved.upserted,
            "skipped_registry_not_synced": saved.skipped_registry_not_synced,
        }
    return summary


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
