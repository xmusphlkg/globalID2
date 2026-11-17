#!/usr/bin/env python3
"""Capture four official sources and replay locally before database ingestion.

The manifest can be passed to scripts/imports/import_surveillance_archive.py.
This command archives originals and writes CSV/audit receipts without DB writes.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.south_asia_sources import SOUTH_ASIA_FOUR_COUNTRIES, south_asia_source


class LocalWriter:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.directory / f"{len(self.artifacts):04d}-{digest}.bin"
        path.write_bytes(payload)
        artifact = SimpleNamespace(relative_path=path.relative_to(ROOT).as_posix(), sha256=digest,
                                   byte_size=len(payload), source_url=source_url, kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        return artifact


async def capture_country(code, *, start_year=None, end_year=None, max_reports=100):
    spec = south_asia_source(code)
    updater = getattr(import_module(f"src.data.processors.{spec['module']}"), spec["updater"])()
    connector = import_module(f"src.services.ingestion.countries.{spec['module']}")
    capture_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    writer = LocalWriter(ROOT / "data/raw" / code.lower() / capture_id)
    output = ROOT / "data/current" / code.lower() / capture_id
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"country_code": code, "source": spec["scope"], "source_id": spec["source_id"],
               "database_writes": False, "public_release_enabled": False, "capture_id": capture_id}
    options = {"start_year": start_year or updater.full_history_start_year, "max_reports": max_reports}
    if end_year is not None:
        options["end_year"] = end_year
    try:
        await connector.collect(writer, source=spec["scope"], **options)
        result = await asyncio.to_thread(connector.organize, writer.artifacts, updater=updater, source=spec["scope"], **options)
        path = output / "observations.csv"
        if result.rows:
            with path.open("w", newline="") as stream:
                csv_writer = csv.DictWriter(stream, fieldnames=sorted({k for row in result.rows for k in row}))
                csv_writer.writeheader()
                csv_writer.writerows(result.rows)
        receipt.update(status="observations_verified" if result.rows else "no_verified_observations",
                       observations=len(result.rows), latest=str(result.source_latest_date) if result.source_latest_date else None,
                       source_audit=result.source_audit, output_csv=str(path.relative_to(ROOT)) if result.rows else None)
    except Exception as exc:
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    manifest = writer.directory / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": [vars(a) for a in writer.artifacts]}, ensure_ascii=False, indent=2) + "\n")
    receipt["manifest"] = str(manifest.relative_to(ROOT))
    (output / "collection_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "source_audit"}, ensure_ascii=False), flush=True)
    return receipt


async def main(args):
    # Separate source collectors may run concurrently; each writes its own
    # immutable country archive and no shared database state.
    results = await asyncio.gather(*(capture_country(c, start_year=args.start_year, end_year=args.end_year,
                                                    max_reports=args.max_reports) for c in args.countries))
    return int(any(r["status"] == "failed" for r in results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+", choices=sorted(SOUTH_ASIA_FOUR_COUNTRIES), default=sorted(SOUTH_ASIA_FOUR_COUNTRIES))
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--max-reports", type=int, default=100, choices=range(1, 2001))
    sys.exit(asyncio.run(main(parser.parse_args())))
