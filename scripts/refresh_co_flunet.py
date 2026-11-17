#!/usr/bin/env python3
"""Capture the complete official CO FluNet response and replay revised years."""
import argparse
import asyncio
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.imports.import_surveillance_archive import import_archive
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from src.services.ingestion.countries.who_flunet import collect


async def run():
    state_dir = ROOT / "data/current/co"
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "flunet-refresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        writer = FileSnapshotWriter(ROOT / "data/raw/co/flunet-refresh" / stamp)
        await collect(writer, country_code="CO")
        # Replay all 2025+ closed weeks daily. This is a small source and also
        # catches older corrections, beyond the minimum trailing 12 weeks.
        result = await import_archive("CO", writer.directory / "manifest.json", apply=True,
            source="who_flunet_weekly", start_year=2025,
            receipt_path=state_dir / "flunet-latest-refresh.json",
            task_metadata={"origin": "co_flunet_automatic_refresh"})
        return {"status": "updated", "records": result["records"], "task_uuid": result["task_uuid"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(asyncio.run(run())))
