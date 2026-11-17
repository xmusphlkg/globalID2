#!/usr/bin/env python3
"""Capture and offline-organize high-frequency sources locally, without database writes."""
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
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.directory / f"{len(self.artifacts):04d}-{digest}.bin"
        path.write_bytes(payload)
        artifact = SimpleNamespace(relative_path=path.as_posix(), sha256=digest,
                                   byte_size=len(payload), source_url=source_url,
                                   kind=kind, metadata_=metadata)
        self.artifacts.append(artifact)
        return artifact


def connector(source, country):
    if source == "flunet":
        from src.services.ingestion.countries import who_flunet as module
        from src.data.processors.who_flunet import WHOFluNetUpdater
        return module, WHOFluNetUpdater(country)
    if source == "ca":
        from src.services.ingestion.countries import ca_respiratory_weekly as module
        from src.data.processors.ca_respiratory_weekly import CARespiratoryWeeklyUpdater
        if country != "CA":
            raise ValueError("PHAC source requires CA")
        return module, CARespiratoryWeeklyUpdater()
    from src.services.ingestion.countries import spf_odisse_weekly as module
    from src.data.processors.spf_odisse_weekly import SPFOdisseWeeklyUpdater
    return module, SPFOdisseWeeklyUpdater(country)


async def main(args):
    capture_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + args.source
    directory = Path("data/raw/frequency-expansion") / capture_id
    output = Path("data/current/frequency-expansion") / capture_id
    output.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(country):
        async with semaphore:
            module, updater = connector(args.source, country)
            writer = LocalWriter(directory / country.lower() / updater.source_scope)
            receipt = {"country_code": country, "source_scope": updater.source_scope,
                       "source_id": updater.ontology_source_id}
            try:
                await module.collect(writer, country_code=country, source=updater.source_scope)
                # The organizer reads checksum-verified artifacts; it does not fetch.
                fetched = module.organize(writer.artifacts, updater=updater, source=updater.source_scope)
                fields = sorted({key for row in fetched.rows for key in row})
                csv_path = output / f"{country.lower()}-{updater.source_scope}.csv"
                with csv_path.open("w", newline="") as stream:
                    writer_csv = csv.DictWriter(stream, fieldnames=fields)
                    writer_csv.writeheader()
                    writer_csv.writerows(fetched.rows)
                receipt.update(status="organized", observations=len(fetched.rows),
                               numeric_observations=sum(r.get("MissingValueStatus") == "reported" for r in fetched.rows),
                               latest=str(fetched.source_latest_date), output_csv=csv_path.as_posix(),
                               source_audit=fetched.source_audit)
            except Exception as error:
                receipt.update(status="failed", error=f"{type(error).__name__}: {error}")
            receipt["artifacts"] = [vars(a) for a in writer.artifacts]
            (writer.directory / "artifacts_manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
            print(json.dumps({k: v for k, v in receipt.items() if k not in {"artifacts", "source_audit"}}, ensure_ascii=False), flush=True)
            return receipt

    receipts = await asyncio.gather(*(one(c.upper()) for c in args.countries))
    target = output / "collection_receipt.json"
    target.write_text(json.dumps({"capture_id": capture_id, "source": args.source,
                                  "database_writes": False, "countries": receipts},
                                 ensure_ascii=False, indent=2, default=str) + "\n")
    print(target)
    return int(any(r["status"] != "organized" for r in receipts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("flunet", "ca", "spf"), required=True)
    parser.add_argument("--countries", nargs="+", required=True)
    parser.add_argument("--concurrency", type=int, default=3, choices=range(1, 9))
    sys.exit(asyncio.run(main(parser.parse_args())))
