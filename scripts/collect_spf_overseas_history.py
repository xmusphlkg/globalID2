#!/usr/bin/env python3
"""Collect/replay SPF overseas history locally; no database or publication writes."""
from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.processors.spf_overseas import SPFOverseasUpdater
from src.services.ingestion.countries.spf_overseas import collect, organize


class LocalWriter:
    def __init__(self, country_code):
        self.directory = ROOT / 'data/raw' / country_code.lower() / 'spf_odisse_history'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        digest = hashlib.sha256(payload).hexdigest()
        path = self.directory / f'{len(self.artifacts):03d}-{digest}.json'
        path.write_bytes(payload)
        item = SimpleNamespace(relative_path=str(path.relative_to(ROOT)), sha256=digest,
            byte_size=len(payload), source_url=source_url, kind=kind, metadata_=metadata)
        self.artifacts.append(item)
        return item


async def run(country, replay):
    writer = LocalWriter(country)
    manifest_path = writer.directory / 'artifacts_manifest.json'
    if replay:
        writer.artifacts = [SimpleNamespace(**x) for x in json.loads(manifest_path.read_text())]
    else:
        await collect(writer, country_code=country)
        manifest_path.write_text(json.dumps([vars(a) for a in writer.artifacts], ensure_ascii=False, indent=2) + '\n')
    updater = SPFOverseasUpdater(country)
    result = organize(writer.artifacts, updater=updater)
    updater.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with updater.output_csv.open('w') as stream:
        output = csv.DictWriter(stream, fieldnames=list(result.rows[0]))
        output.writeheader()
        output.writerows(result.rows)
    result.source_audit['raw_directory'] = str(writer.directory.relative_to(ROOT))
    result.source_audit['artifacts_manifest'] = str(manifest_path.relative_to(ROOT))
    (updater.output_csv.parent / 'spf_odisse_source_audit.json').write_text(json.dumps(result.source_audit, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result.source_audit), flush=True)


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--countries', nargs='+', choices=['GP', 'MQ', 'GF'], default=['GP', 'MQ', 'GF'])
    p.add_argument('--replay', action='store_true')
    args = p.parse_args()
    for country in args.countries:
        await run(country, args.replay)


if __name__ == '__main__':
    asyncio.run(main())
