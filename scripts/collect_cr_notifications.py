#!/usr/bin/env python3
"""Collect real Costa Rica originals and offline parse; no database writes."""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater
from src.services.ingestion.countries import cr_notifications as connector


class LocalWriter:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        suffix = ".pdf" if payload.startswith(b"%PDF") else ".json" if kind == "archive_json" else ".html"
        path = self.directory / (digest + suffix)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Local immutable artifact integrity failure")
        path.write_bytes(payload)
        artifact = SimpleNamespace(relative_path=str(path), sha256=digest, byte_size=len(payload), source_url=source_url,
                                   kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        # Keep a durable partial receipt even when a process is interrupted.
        (self.directory / "artifacts_manifest.json").write_text(json.dumps([vars(a) for a in self.artifacts], ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"captured": len(self.artifacts), "kind": kind, "url": source_url, "bytes": len(payload)}), flush=True)
        return artifact


async def main(args):
    directory = Path(args.directory or f"data/raw/cr/moh_notifications_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    writer = LocalWriter(directory)
    if args.replay:
        writer.artifacts = [SimpleNamespace(**a) for a in json.loads((directory / "artifacts_manifest.json").read_text())]
    else:
        await connector.collect(writer, start_year=args.start_year, end_year=args.end_year)
    updater = CostaRicaNotificationsUpdater()
    result = connector.organize(writer.artifacts, updater=updater)
    output = updater.output_csv
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in result.rows for key in row))
    with output.open("w", newline="", encoding="utf-8") as stream:
        csv_writer = csv.DictWriter(stream, fieldnames=fields)
        csv_writer.writeheader()
        csv_writer.writerows(result.rows)
    audit = dict(result.source_audit, raw_directory=str(directory), csv_path=str(output), collected_at=datetime.now(timezone.utc).isoformat())
    (output.parent / "cr_moh_source_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"observations": len(result.rows), "raw_directory": str(directory), "audit": str(output.parent / "cr_moh_source_audit.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--directory")
    parser.add_argument("--replay", action="store_true")
    asyncio.run(main(parser.parse_args()))
