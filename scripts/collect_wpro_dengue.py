#!/usr/bin/env python3
"""Capture WHO's currently listed dengue PDFs and audit exact numerical coverage."""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.processors.wpro_dengue import WPRODengueUpdater
from src.services.ingestion.countries import wpro_dengue


class LocalWriter:
    def __init__(self, directory):
        self.directory = directory
        self.artifacts = []
        directory.mkdir(parents=True, exist_ok=True)
    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        extension = {'html_report': 'html', 'cda_pdf': 'pdf'}.get(kind, 'json')
        path = self.directory / f'{len(self.artifacts):03d}-{digest[:20]}.{extension}'
        path.write_bytes(payload)
        artifact = SimpleNamespace(relative_path=path.as_posix(), sha256=digest, byte_size=len(payload),
                                   source_url=source_url, kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        return artifact


async def collect(code):
    directory = Path(f'data/raw/{code.lower()}/wpro_dengue_current/{uuid4().hex}')
    writer = LocalWriter(directory)
    await wpro_dengue.collect(writer, country_code=code)
    manifest = directory / 'artifacts_manifest.json'
    manifest.write_text(json.dumps([vars(a) for a in writer.artifacts], ensure_ascii=False, indent=2) + '\n')
    result = await asyncio.to_thread(wpro_dengue.organize, writer.artifacts,
                                    updater=WPRODengueUpdater(code), country_code=code)
    target = Path(f'data/current/{code.lower()}')
    target.mkdir(parents=True, exist_ok=True)
    with (target / 'wpro_dengue.csv').open('w', newline='') as file:
        csv_writer = csv.DictWriter(file, fieldnames=list(result.rows[0]))
        csv_writer.writeheader()
        csv_writer.writerows(result.rows)
    audit = {'country_code': code, 'raw_directory': directory.as_posix(), 'status': 'organized',
             'observations': len(result.rows), 'source_audit': result.source_audit}
    (target / 'wpro_dengue_source_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(audit, ensure_ascii=False), flush=True)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--countries', nargs='+', choices=['LA', 'VN'], default=['LA', 'VN'])
    args = parser.parse_args()
    async def run():
        return await asyncio.gather(*(collect(code) for code in args.countries))
    asyncio.run(run())


if __name__ == '__main__':
    main()
