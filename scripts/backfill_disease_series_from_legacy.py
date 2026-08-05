#!/usr/bin/env python3
"""Recover lossless source-series facts from legacy ``disease_records`` rows.

Legacy processors sometimes stored one source row as a JSON object and
sometimes stored several source rows as a JSON array.  This command expands
both shapes (including JSON-encoded strings), resolves every recovered row
through the disease-series registry, and optionally upserts the resulting
observations.  It is a dry run unless ``--apply`` is supplied.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.data.storage import SeriesObservationStore  # noqa: E402


DEFAULT_BATCH_SIZE = 500
_MAX_JSON_DECODE_DEPTH = 4


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill disease source-series facts from disease_records.raw_data."
        )
    )
    parser.add_argument("--country", required=True, help="Two-letter country code.")
    parser.add_argument(
        "--source-id",
        help="Optional ontology source ID when a country has multiple sources.",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=DEFAULT_BATCH_SIZE,
        help=f"Legacy records processed per batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upsert matched observations. Without this flag, only report counts.",
    )
    return parser.parse_args(argv)


def recover_source_rows(
    raw_data: object,
    *,
    record_time: datetime | None = None,
    record_cases: object = None,
) -> tuple[list[dict[str, Any]], int]:
    """Expand a legacy raw payload into source rows and an invalid-item count."""

    rows, skipped = _expand_raw_value(raw_data)
    return [
        _add_legacy_fallbacks(
            row,
            record_time=record_time,
            record_cases=record_cases,
        )
        for row in rows
    ], skipped


def _expand_raw_value(
    value: object,
    *,
    depth: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if isinstance(value, str):
        if depth >= _MAX_JSON_DECODE_DEPTH:
            return [], 1
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [], 1
        return _expand_raw_value(decoded, depth=depth + 1)

    if isinstance(value, Mapping):
        return [dict(value)], 0

    if isinstance(value, (list, tuple)):
        if not value:
            return [], 1
        rows: list[dict[str, Any]] = []
        skipped = 0
        for item in value:
            item_rows, item_skipped = _expand_raw_value(item, depth=depth)
            rows.extend(item_rows)
            skipped += item_skipped
        return rows, skipped

    return [], 1


def _add_legacy_fallbacks(
    row: dict[str, Any],
    *,
    record_time: datetime | None,
    record_cases: object,
) -> dict[str, Any]:
    recovered = dict(row)
    if record_time is not None and not _has_report_time(recovered):
        recovered["Date"] = record_time
    if record_cases is not None and not str(recovered.get("Cases") or "").strip():
        recovered["Cases"] = record_cases
    return recovered


def _has_report_time(row: Mapping[str, Any]) -> bool:
    if any(str(row.get(key) or "").strip() for key in ("Date", "date", "time")):
        return True
    if (
        any(
            str(row.get(key) or "").strip()
            for key in ("MMWRYear", "Current MMWR Year")
        )
        and any(
            str(row.get(key) or "").strip()
            for key in ("MMWRWeek", "MMWR WEEK")
        )
    ):
        return True
    return bool(row.get("Year") and row.get("Month"))


async def iter_legacy_batches(
    db: AsyncSession,
    country_code: str,
    batch_size: int,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Read stable pages of legacy records for one canonical country."""

    offset = 0
    statement = text(
        """
        SELECT
            dr.time AS record_time,
            dr.cases AS record_cases,
            dr.raw_data AS raw_data
        FROM disease_records AS dr
        JOIN countries AS country ON country.id = dr.country_id
        WHERE country.code = :country_code
        ORDER BY dr.time, dr.disease_id, dr.country_id
        LIMIT :batch_size OFFSET :offset
        """
    )
    while True:
        result = await db.execute(
            statement,
            {
                "country_code": country_code,
                "batch_size": batch_size,
                "offset": offset,
            },
        )
        batch = [dict(row) for row in result.mappings().all()]
        if not batch:
            return
        yield batch
        if len(batch) < batch_size:
            return
        offset += len(batch)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    country_code = str(args.country or "").strip().upper()
    if len(country_code) != 2 or not country_code.isalpha():
        raise ValueError("--country must be a two-letter code")
    batch_size = int(args.batch_size)
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than zero")
    source_id = str(args.source_id or "").strip() or None
    apply = bool(args.apply)
    geography_key = f"country:{country_code}:national"

    store = SeriesObservationStore()
    records_scanned = 0
    source_rows_recovered = 0
    raw_data_invalid = 0
    mappable = 0
    written = 0
    registry_not_synced = 0
    skipped_unmatched = 0
    skipped_ambiguous = 0
    skipped_invalid_observation = 0
    skipped_duplicate = 0
    matched_by_series: Counter[str] = Counter()

    async with get_db() as db:
        async for legacy_batch in iter_legacy_batches(
            db,
            country_code,
            batch_size,
        ):
            records_scanned += len(legacy_batch)
            source_rows: list[dict[str, Any]] = []
            for legacy_record in legacy_batch:
                recovered, skipped = recover_source_rows(
                    legacy_record.get("raw_data"),
                    record_time=legacy_record.get("record_time"),
                    record_cases=legacy_record.get("record_cases"),
                )
                source_rows.extend(recovered)
                raw_data_invalid += skipped

            source_rows_recovered += len(source_rows)
            if not source_rows:
                continue

            built = store.build_observations(
                source_rows,
                country_code,
                source_id=source_id,
                geography_key=geography_key,
            )
            batch_mappable = len(built.observations)
            batch_known_skipped = (
                built.skipped_unmatched
                + built.skipped_ambiguous
                + built.skipped_invalid
            )
            batch_duplicate = max(
                0,
                len(source_rows) - batch_mappable - batch_known_skipped,
            )
            mappable += batch_mappable
            matched_by_series.update(
                observation["series_code"] for observation in built.observations
            )
            skipped_unmatched += built.skipped_unmatched
            skipped_ambiguous += built.skipped_ambiguous
            skipped_invalid_observation += built.skipped_invalid
            skipped_duplicate += batch_duplicate

            if apply and batch_mappable:
                saved = await store.save_rows(
                    db,
                    source_rows,
                    country_code,
                    source_id=source_id,
                    geography_key=geography_key,
                )
                written += saved.upserted
                registry_not_synced += saved.skipped_registry_not_synced

    raw_items_scanned = source_rows_recovered + raw_data_invalid
    skipped = (
        raw_data_invalid
        + skipped_unmatched
        + skipped_ambiguous
        + skipped_invalid_observation
        + skipped_duplicate
    )
    return {
        "mode": "apply" if apply else "dry_run",
        "country_code": country_code,
        "source_id": source_id,
        "geography_key": geography_key,
        "batch_size": batch_size,
        "scanned": records_scanned,
        "records_scanned": records_scanned,
        "raw_items_scanned": raw_items_scanned,
        "source_rows_recovered": source_rows_recovered,
        "mappable": mappable,
        "matched_by_series": dict(sorted(matched_by_series.items())),
        "skipped": skipped,
        "written": written,
        "would_write": mappable,
        "skip_breakdown": {
            "raw_data_invalid": raw_data_invalid,
            "unmatched": skipped_unmatched,
            "ambiguous": skipped_ambiguous,
            "invalid_observation": skipped_invalid_observation,
            "duplicate_observation": skipped_duplicate,
        },
        "write_skip_breakdown": {
            "registry_not_synced": registry_not_synced,
        },
    }


def main() -> None:
    summary = asyncio.run(run(parse_args()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
