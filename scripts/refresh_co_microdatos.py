#!/usr/bin/env python3
"""Refresh the closed-year catalogue and retain private microdata strata."""
import argparse
import asyncio
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.imports.import_surveillance_archive import import_archive
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from src.services.ingestion.co_microdatos_refresh import collect


async def run():
    directory = ROOT / "data/current/co"
    with (directory / "microdatos-refresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        last_closed = datetime.now(ZoneInfo("America/Bogota")).year - 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        writer = FileSnapshotWriter(ROOT / "data/raw/co/microdatos-refresh" / stamp)
        await collect(writer, source="co_ins_sivigila_microdatos", start_year=2025,
                      end_year=last_closed, force=True)
        result = await import_archive("CO", writer.directory / "manifest.json", apply=True,
            source="co_ins_sivigila_microdatos", start_year=2025,
            receipt_path=directory / "microdatos-latest-refresh.json",
            task_metadata={"origin": "co_microdatos_automatic_refresh"})
        return {"status": "private_source_updated", "records": result["records"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(asyncio.run(run())))
