#!/usr/bin/env python3
"""Capture/replay European high-frequency sources without database mutations."""
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


class LocalWriter:
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.resolve().is_relative_to((ROOT / "data/raw").resolve()):
            raise ValueError("Local captures must remain beneath data/raw")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.directory / f"{len(self.artifacts):04d}-{digest[:16]}.bin"
        path.write_bytes(payload)
        artifact = SimpleNamespace(relative_path=str(path.relative_to(ROOT) if path.is_absolute() else path), sha256=digest,
            byte_size=len(payload), source_url=source_url, kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        (self.directory / "artifacts_manifest.json").write_text(json.dumps([vars(a) for a in self.artifacts], ensure_ascii=False, indent=2))
        return artifact


async def main(args):
    if args.source == "ecdc_respiratory_weekly":
        from src.data.processors.ecdc_respiratory import ECDCRespiratoryUpdater
        from src.services.ingestion.countries import ecdc_respiratory as connector
        updater = ECDCRespiratoryUpdater(country_code=args.country)
    else:
        from src.data.processors.eu_monthly import EUMonthlyUpdater
        from src.services.ingestion.countries import eu_monthly as connector
        updater = EUMonthlyUpdater(country_code=args.country)
        if updater.source_scope != args.source:
            raise ValueError("Country and source scope disagree")
    directory = args.capture_directory or ROOT / "data/raw" / args.source / (args.country.lower() + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if args.offline_organize:
        artifacts = [SimpleNamespace(**item) for item in json.loads((Path(directory) / "artifacts_manifest.json").read_bytes())]
    else:
        writer = LocalWriter(directory)
        artifacts = await connector.collect(writer, country_code=args.country, source=args.source, start_year=args.start_year, end_year=args.end_year)
    result = connector.organize(artifacts, updater=updater, source=args.source, start_year=args.start_year, end_year=args.end_year)
    result.source_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in result.rows for k in row})
    with result.source_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(result.rows)
    audit_path = result.source_csv.with_suffix(".audit.json")
    audit_path.write_text(json.dumps(result.source_audit, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"rows": len(result.rows), "source_csv": str(result.source_csv), "source_audit": str(audit_path), "capture_directory": str(directory), "latest": str(result.source_latest_date)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", required=True)
    parser.add_argument("--source", required=True, choices=["ecdc_respiratory_weekly", "cz_szu_monthly", "ee_terviseamet_monthly"])
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--capture-directory", type=Path)
    parser.add_argument("--offline-organize", action="store_true")
    asyncio.run(main(parser.parse_args()))
