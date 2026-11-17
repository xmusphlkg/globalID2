#!/usr/bin/env python3
"""Remove a volatile CKAN timestamp from Chile ENO observation identities."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core.database import dispose_database, get_db
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.data.storage.series.identity import _dimension_key
from src.domain.disease_ontology import (
    DiseaseSeriesObservation, DiseaseSeriesObservationRevision, DiseaseSurveillanceSeries,
)

VOLATILE_FIELD = "source_catalog_last_modified"
SOURCE_ID = "SRC_CL_ENO"


def normalized(row):
    dimensions = dict(row.dimensions or {})
    dimensions.pop(VOLATILE_FIELD, None)
    raw_data = dict(row.raw_data or {})
    if "Dimensions" in raw_data:
        raw_data["Dimensions"] = json.dumps(dimensions, ensure_ascii=False, sort_keys=True)
    return dimensions, _dimension_key(dimensions), raw_data


async def migrate(*, apply: bool = False):
    async with get_db() as db:
        if apply:
            await acquire_country_data_mutation_lock(db, "CL")
        collections = []
        for model in (DiseaseSeriesObservation, DiseaseSeriesObservationRevision):
            statement = (
                select(model).join(DiseaseSurveillanceSeries, model.series_code == DiseaseSurveillanceSeries.series_code)
                .where(DiseaseSurveillanceSeries.country_code == "CL", DiseaseSurveillanceSeries.source_system == SOURCE_ID)
            )
            rows = (await db.execute(statement)).scalars().all()
            keys = Counter(
                (row.time, row.series_code, row.geography_key, normalized(row)[1],
                 row.recorded_at if model is DiseaseSeriesObservationRevision else None)
                for row in rows
            )
            if any(count > 1 for count in keys.values()):
                raise ValueError(f"Normalized {model.__tablename__} identities collide")
            changed = 0
            for row in rows:
                if VOLATILE_FIELD not in (row.dimensions or {}):
                    continue
                dimensions, dimension_key, raw_data = normalized(row)
                if apply:
                    row.dimensions = dimensions
                    row.dimension_key = dimension_key
                    row.raw_data = raw_data
                changed += 1
            collections.append({"table": model.__tablename__, "rows": len(rows), "changed": changed})
        if apply:
            await db.commit()
    return {"mode": "applied" if apply else "preview", "source_id": SOURCE_ID,
            "checked_at": datetime.now(timezone.utc).isoformat(), "collections": collections}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run():
        try:
            receipt = await migrate(apply=args.apply)
            if args.apply:
                target = ROOT / "data/current/cl/dimension_identity_migration.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(receipt, ensure_ascii=False))
        finally:
            await dispose_database()

    asyncio.run(run())


if __name__ == "__main__":
    main()
