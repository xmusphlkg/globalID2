#!/usr/bin/env python3
"""Replay AGES artifacts into the existing native series and verify identities."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from scripts.import_high_frequency_sources import HighFrequencyReplay
from scripts.onboard_remaining_surveillance import create_foreground_import_task, fail_foreground_import
from src.core.database import get_db, dispose_database
from src.data.processors.at import ATMonthlyUpdater
from src.domain.task import TaskStatus
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import at as connector
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance

STATIC_SERIES = "SER_AT_AGES_RADAR_SOURCE_NATIVE"


def identity(time, dimensions):
    return str(time)[:10], json.dumps(dimensions, sort_keys=True, ensure_ascii=False)


async def snapshot():
    async with get_db() as db:
        rows = (await db.execute(text("""
            SELECT o.series_code,o.time,o.geography_key,o.dimension_key,o.dimensions,
                   o.value,o.unit,o.raw_data,o.metadata,s.definition_version,
                   s.metric_type,s.reporting_basis,s.temporal_granularity
            FROM disease_series_observations o
            JOIN disease_surveillance_series s USING(series_code)
            WHERE s.country_code='AT' AND s.source_system='SRC_AT_AGES_RADAR'
            ORDER BY o.series_code,o.time,o.geography_key,o.dimension_key
        """))).mappings().all()
    return [dict(r) for r in rows]


def holding_fingerprint(rows):
    holding = [r for r in rows if r["series_code"] != STATIC_SERIES]
    return hashlib.sha256(json.dumps(holding, sort_keys=True, default=str).encode()).hexdigest()


async def main(args):
    replay = HighFrequencyReplay(connector, args.manifest)
    updater = ATMonthlyUpdater(output_csv=ROOT / "data/current/at/austria_ages_radar_monthly.csv")
    options = {"output_csv": updater.output_csv, "force": True}
    organized = connector.organize(replay.originals, updater=updater, source="ages_radar", **options)
    expected = {identity(r["Date"], json.loads(r["Dimensions"])): r for r in organized.rows}
    if len(expected) != len(organized.rows) or any(r["SourceDiseaseCode"] != "__source_native__" for r in expected.values()):
        raise ValueError("AGES capture must preserve unique category/month native identities")
    before = await snapshot()
    task = await create_foreground_import_task("AT", "ages_radar")
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=replay, source="ages_radar", force=True, process=True,
                connector_options=options)
            after = await snapshot()
            actual = {identity(r["time"], r["dimensions"]): r for r in after if r["series_code"] == STATIC_SERIES}
            if len(actual) != sum(r["series_code"] == STATIC_SERIES for r in after):
                raise ValueError("AGES static series contains duplicate category/month identities")
            missing, invalid = [], []
            for key, row in expected.items():
                stored = actual.get(key)
                if stored is None:
                    missing.append(key)
                    continue
                value = None if row["Cases"] == "" else Decimal(row["Cases"])
                if (stored["value"] != value or stored["geography_key"] != row["GeographyKey"]
                        or stored["unit"] != "count" or stored["temporal_granularity"] != "monthly"
                        or stored["definition_version"] != "AT_AGES_RADAR_source_native"
                        or stored["metric_type"] != "case_notifications"
                        or any(stored["raw_data"].get(k) != v for k, v in row.items())):
                    invalid.append(key)
            before_static = {identity(r["time"], r["dimensions"]) for r in before if r["series_code"] == STATIC_SERIES}
            history_lost = before_static - set(actual)
            holding_unchanged = holding_fingerprint(before) == holding_fingerprint(after)
            if missing or invalid or history_lost or not holding_unchanged:
                raise ValueError(f"AGES acceptance failed: missing={len(missing)}, invalid={len(invalid)}, history_lost={len(history_lost)}, holding_unchanged={holding_unchanged}; samples={invalid[:2]}")
            receipt = {"country_code": "AT", "source": "ages_radar", "status": "completed",
                "task_uuid": task.task_uuid, "manifest": str(args.manifest), "ingestion": asdict(result),
                "captured_observations": len(expected), "static_observations_before": len(before_static),
                "static_observations_after": len(actual), "retained_historical_facts": len(set(actual)-set(expected)),
                "holding_facts_unchanged": holding_unchanged,
                "holding_observations": sum(r["series_code"] != STATIC_SERIES for r in after),
                "missing_count": 0, "mismatch_count": 0, "duplicate_count": 0,
                "checked_at": datetime.now(timezone.utc).isoformat(), "pass": True}
            async with get_db() as db:
                owned = await lease.owned_task(db)
                owned.status = TaskStatus.COMPLETED
                owned.progress = 100
                owned.completed_at = datetime.now(timezone.utc)
                owned.output_data = receipt
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str)+"\n")
            print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    except BaseException as error:
        await fail_foreground_import(task.task_uuid, lease, error)
        raise
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/raw/frequency-expansion/20261117T013647Z-at/at/ages_radar/artifacts_manifest.json"))
    parser.add_argument("--receipt", type=Path, default=Path("data/current/frequency-expansion/at-database-import-receipt.json"))
    asyncio.run(main(parser.parse_args()))
