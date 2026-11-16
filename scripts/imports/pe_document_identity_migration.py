#!/usr/bin/env python3
"""Idempotently stamp captured document identity on the first Peru import.

Only current observations whose exact URL/SHA exists in immutable PE archive
manifests are eligible. Numeric values and immutable revision history remain
untouched. A receipt preserves old/new identities; CSV repair is restartable.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select
from src.core.database import get_db, dispose_database
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.domain import DiseaseSeriesObservation as Observation, DiseaseSurveillanceSeries as Series
from src.data.storage.series.identity import _dimension_key


async def migrate(*, apply=False):
    allowed = set()
    for manifest in (ROOT / "data/raw/pe/cdc-history").glob("*/batch-*/manifest.json"):
        for artifact in json.loads(manifest.read_text())["artifacts"]:
            if artifact["kind"] == "cda_pdf":
                allowed.add((artifact["source_url"], artifact["sha256"]))
    changes = []
    try:
        async with get_db() as db:
            await acquire_country_data_mutation_lock(db, "PE")
            rows = (await db.execute(select(Observation).join(Series, Observation.series_code == Series.series_code)
                .where(Series.country_code == "PE", Series.source_system == "SRC_PE_CDC"))).scalars().all()
            for row in rows:
                dimensions = dict(row.dimensions or {})
                if "source_report_sha256" in dimensions:
                    continue
                raw = dict(row.raw_data or {}); url = raw["SourceURL"]; digest = raw["RawSHA256"]
                if (url, digest) not in allowed:
                    raise ValueError("Observation is not backed by the authorized immutable PE manifests")
                dimensions.update(source_report_url=url, source_report_sha256=digest)
                new_key = _dimension_key(dimensions)
                if any(other.id != row.id and other.time == row.time and other.series_code == row.series_code
                    and other.geography_key == row.geography_key and other.dimension_key == new_key for other in rows):
                    raise ValueError("New observation identity already exists; explicit reconciliation required")
                changes.append({"observation_id": row.id, "time": row.time.isoformat(), "series_code": row.series_code,
                    "old_dimension_key": row.dimension_key, "new_dimension_key": new_key, "source_url": url, "sha256": digest})
                if apply:
                    raw["Dimensions"] = json.dumps(dimensions, ensure_ascii=False, sort_keys=True)
                    row.raw_data = raw; row.dimensions = dimensions; row.dimension_key = new_key
                    row.metadata_ = {**(row.metadata_ or {}), "identity_metadata_migration": "pe_document_provenance_v1"}
        csv_updates = 0
        csv_path = ROOT / "data/current/pe/peru_cdc_weekly.csv"
        if csv_path.exists():
            with csv_path.open() as stream:
                reader = csv.DictReader(stream); fields = reader.fieldnames; csv_rows = list(reader)
            for row in csv_rows:
                dimensions = json.loads(row["Dimensions"])
                if "source_report_sha256" not in dimensions:
                    if (row["SourceURL"], row["RawSHA256"]) not in allowed:
                        raise ValueError("CSV fact lacks immutable PE evidence")
                    dimensions.update(source_report_url=row["SourceURL"], source_report_sha256=row["RawSHA256"])
                    row["Dimensions"] = json.dumps(dimensions, ensure_ascii=False, sort_keys=True); csv_updates += 1
            if apply and csv_updates:
                temp = csv_path.with_suffix(".csv.tmp")
                with temp.open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(csv_rows)
                temp.replace(csv_path)
        receipt = {"migration": "pe_document_provenance_v1", "completed_at": datetime.now(timezone.utc).isoformat(),
            "apply": apply, "current_observations_updated": len(changes), "csv_rows_updated": csv_updates,
            "numeric_values_changed": False, "old_revision_history_preserved": True, "changes": changes}
        if apply:
            path = ROOT / "data/current/pe/document_identity_migration_receipt.json"
            if path.exists() and not changes:
                previous = json.loads(path.read_text()); previous["csv_rows_updated"] = csv_updates
                previous["last_idempotence_check"] = receipt["completed_at"]; receipt = previous
            temp = path.with_suffix(".json.tmp"); temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)); temp.replace(path)
        print(json.dumps({"db_updates_this_run": len(changes), "csv_updates_this_run": csv_updates, "apply": apply}))
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(migrate(apply=parser.parse_args().apply))
