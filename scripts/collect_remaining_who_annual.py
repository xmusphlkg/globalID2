#!/usr/bin/env python3
"""Capture country-reported WHO annual history locally, without database writes.

Artifacts are immutable, checksummed and replayed by the production organizer.
This command does not enable public release or create automation jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.who_annual import COUNTRY_CONTRACTS
from src.data.processors.who_annual import WHOAnnualUpdater
from src.services.ingestion.countries.who_annual import collect, organize


class LocalArtifactWriter:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.directory / f"{len(self.artifacts):04d}-{digest[:16]}.json"
        # Existing captures are immutable; a new invocation has its own directory.
        with path.open("xb") as stream:
            stream.write(payload)
        artifact = SimpleNamespace(relative_path=path.as_posix(), sha256=digest,
                                   byte_size=len(payload), source_url=source_url,
                                   kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        self.save_manifest()
        return artifact

    def save_manifest(self):
        manifest = self.directory / "artifacts_manifest.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps([vars(a) for a in self.artifacts], ensure_ascii=False, indent=2) + "\n")
        temporary.replace(manifest)


async def collect_country(code, semaphore):
    async with semaphore:
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        directory = Path("data/raw") / code.lower() / "who_gho_full_history" / identifier
        writer = LocalArtifactWriter(directory)
        receipt = {"country_code": code, "raw_directory": directory.as_posix()}
        output = Path("data/current") / code.lower()
        output.mkdir(parents=True, exist_ok=True)
        try:
            await collect(writer, country_code=code)
            result = organize(writer.artifacts, updater=WHOAnnualUpdater(code))
            fields = list(dict.fromkeys(k for row in result.rows for k in row))
            path = output / "who_gho_annual.csv"
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", newline="") as stream:
                csv_writer = csv.DictWriter(stream, fieldnames=fields)
                csv_writer.writeheader()
                csv_writer.writerows(result.rows)
            temporary.replace(path)
            receipt.update(status="organized", observations=len(result.rows), source_audit=result.source_audit)
        except Exception as error:
            receipt.update(status="failed", error=f"{type(error).__name__}: {error}", captured_artifacts=len(writer.artifacts))
        (output / "who_gho_source_audit.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k:v for k,v in receipt.items() if k != "source_audit"}), flush=True)
        return receipt


async def main_async(args):
    unknown = set(args.countries) - set(COUNTRY_CONTRACTS)
    if unknown:
        raise ValueError(f"Unreviewed countries: {unknown}")
    semaphore = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*(collect_country(code, semaphore) for code in args.countries))
    path = Path("reports/onboarding/remaining_who_annual_collection.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"collected_at":datetime.now(timezone.utc).isoformat(), "countries":results}, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(r["status"] == "organized" for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+", default=list(COUNTRY_CONTRACTS))
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
