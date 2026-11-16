#!/usr/bin/env python3
"""Archive official Argentina HIV annual originals and organize them offline.

No database writes. The generated manifest can be replayed by the standard
import_surveillance_archive.py --country AR --source ar_moh_hiv_annual entry.
"""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.ar_hiv import DEFAULT_SCOPE, HISTORY_START_YEAR
from src.data.processors.ar_hiv import ArgentinaHIVAnnualUpdater
from src.services.ingestion.countries.ar_hiv import collect, organize
from scripts.imports.import_surveillance_archive import load_archive_manifest

OUTPUT = ROOT / "data/current/ar/hiv_annual"


def resolve_manifest_path(explicit=None, *, output=OUTPUT):
    """Resolve the latest immutable raw manifest, never the current copy.

    The ordinary importer intentionally rejects manifests outside data/raw/ar.
    source_audit records the original path; load_archive_manifest still enforces
    that boundary and verifies every original file before organization.
    """
    if explicit is not None:
        return Path(explicit).resolve()
    audit_path = Path(output) / "source_audit.json"
    try:
        recorded = json.loads(audit_path.read_text())["artifacts_manifest"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("No immutable AR HIV manifest recorded; run live collection first or supply --manifest") from exc
    if not isinstance(recorded, str) or not recorded.strip():
        raise ValueError("Recorded AR HIV raw manifest path is invalid")
    path = Path(recorded)
    return path if path.is_absolute() else ROOT / path


class LocalArchiveWriter:
    """Immutable originals for later registration by the ordinary task runner."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        extension = {"archive_json": "json", "historical_csv": "csv", "workbook_xlsx": "xlsx", "tls_intermediate": "pem"}[kind]
        path = self.directory / f"{(metadata or {})['endpoint']}-{digest}.{extension}"
        with path.open("xb") as stream:
            stream.write(payload)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise OSError("New archive file failed integrity verification")
        artifact = SimpleNamespace(relative_path=path.relative_to(ROOT).as_posix(),
            sha256=digest, byte_size=len(payload), source_url=source_url, kind=kind,
            metadata_=dict(metadata or {}))
        self.artifacts.append(artifact)
        return artifact


async def run(args):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.offline_organize:
        manifest_path = resolve_manifest_path(args.manifest, output=OUTPUT)
        artifacts = load_archive_manifest(manifest_path, "AR")
    else:
        if args.manifest:
            raise ValueError("--manifest selects an existing archive and requires --offline-organize")
        archive = ROOT / "data/raw/ar/hiv_annual" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        writer = LocalArchiveWriter(archive)
        artifacts = await collect(writer)
        records = [vars(artifact) for artifact in artifacts]
        payload = json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        manifest_path = archive / "artifacts_manifest.json"
        manifest_path.write_text(payload)
        (OUTPUT / "artifacts_manifest.json").write_text(payload)
    updater = ArgentinaHIVAnnualUpdater()
    fetched = organize(artifacts, updater=updater, source=DEFAULT_SCOPE, start_year=args.start_year)
    target = OUTPUT / "argentina_hiv_annual.csv"
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fetched.rows[0]))
        writer.writeheader()
        writer.writerows(fetched.rows)
    audit = {**fetched.source_audit, "organized_rows": len(fetched.rows),
             "source_latest_date": str(fetched.source_latest_date),
             "artifacts_manifest": str(manifest_path.relative_to(ROOT)),
             "offline_organization": args.offline_organize,
             "database_imported": False, "published": False}
    (OUTPUT / "source_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-organize", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
