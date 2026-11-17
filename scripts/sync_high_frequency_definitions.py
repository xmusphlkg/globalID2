#!/usr/bin/env python3
"""Synchronize only reviewed high-frequency definitions; never rewrite facts."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import insert, JSONB
from src.core.database import get_db, dispose_database
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES
from src.domain import DiseaseSurveillanceSeries
from src.ontology import load_disease_ontology
from src.services.disease_ontology_sync_service import build_disease_ontology_sync_payload


async def run(*, apply=False):
    registry = load_disease_ontology()
    source_ids = {item.source_id for item in HIGH_FREQUENCY_SOURCES.values()} | {"SRC_AT_AGES_RADAR"}
    definitions = [row for row in build_disease_ontology_sync_payload(registry).surveillance_series
                   if row["source_system"] in source_ids]
    table = DiseaseSurveillanceSeries.__table__
    changes = []
    for country in sorted({row["country_code"] for row in definitions}):
        wanted = [row for row in definitions if row["country_code"] == country]
        async with get_db() as db:
            await acquire_country_data_mutation_lock(db, country)
            stored = {row["series_code"]: dict(row) for row in (await db.execute(
                select(table).where(table.c.series_code.in_([row["series_code"] for row in wanted])))).mappings()}
            for row in wanted:
                previous = stored.get(row["series_code"])
                different = {key: {"before": previous.get(key) if previous else None, "after": value}
                             for key, value in row.items()
                             if key != "metadata" and (previous is None or previous.get(key) != value)}
                if different:
                    changes.append({"country_code": country, "series_code": row["series_code"], "fields": different})
            if apply:
                for offset in range(0, len(wanted), 100):
                    statement = insert(table).values(wanted[offset:offset + 100])
                    updates = {key: statement.excluded[key] for key in wanted[0] if key not in {"series_code", "metadata"}}
                    updates["metadata"] = cast(table.c.metadata, JSONB).op("||")(cast(statement.excluded.metadata, JSONB))
                    await db.execute(statement.on_conflict_do_update(index_elements=["series_code"], set_=updates))
            else:
                await db.rollback()
    return {"mode": "applied" if apply else "preview", "definition_count": len(definitions),
            "changed_definition_count": len(changes), "changes": changes,
            "observation_mutations": 0}


async def main(args):
    try:
        result = await run(apply=args.apply)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key != "changes"}, ensure_ascii=False))
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
