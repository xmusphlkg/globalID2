"""Capture Chile MINSAL's official ENO resources for verified historical replay.

This command writes immutable raw files and a review CSV. Database storage is
performed by import_surveillance_archive.py against the resulting manifest.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from src.data.crawlers.cl import HISTORY_START_YEAR
from src.data.processors.cl import ChileENOHistoricalUpdater
from src.services.ingestion.countries.cl import collect, organize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--replay-manifest", type=Path)
    parser.add_argument("--capture-only", action="store_true")
    args = parser.parse_args()

    if args.replay_manifest:
        manifest_path = args.replay_manifest.resolve()
        artifacts = [SimpleNamespace(**entry) for entry in json.loads(manifest_path.read_text())["artifacts"]]
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        writer = FileSnapshotWriter((args.raw_directory or ROOT / "data/raw/cl/eno-history" / stamp).resolve())
        asyncio.run(collect(writer, start_year=args.start_year, end_year=args.end_year))
        manifest_path = writer.directory / "manifest.json"
        artifacts = writer.artifacts
    print(f"Captured {len(artifacts)} artifacts; manifest {manifest_path}", flush=True)
    if args.capture_only:
        return

    result = organize(artifacts, updater=ChileENOHistoricalUpdater(),
                      start_year=args.start_year, end_year=args.end_year)
    result.source_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.source_csv.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result.rows[0]))
        writer.writeheader()
        writer.writerows(result.rows)
    temporary.replace(result.source_csv)
    result.source_audit["raw_manifest"] = manifest_path.relative_to(ROOT).as_posix()
    result.source_audit["database_registered"] = False
    (result.source_csv.parent / "source_audit.json").write_text(
        json.dumps(result.source_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(result.script_logs), flush=True)
    print(f"Review CSV {result.source_csv}; database import pending", flush=True)


if __name__ == "__main__":
    main()
