"""Archive and organize all published CamEWARN history; does not write case DB.

The manifest records immutable raw bytes for subsequent controlled DB import.
Use --reuse-cache only to reuse already downloaded upstream archive snapshots;
this is explicitly recorded as cached_snapshot, never a successful live fetch.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.kh import CambodiaCamEWARNCrawler, HISTORY_START_YEAR
from src.data.processors.kh import CambodiaCamEWARNWeeklyUpdater
from src.services.ingestion.countries.kh import collect, organize


class FileSnapshotWriter:
    """Durable raw snapshot before organization, with explicit unregistered state."""
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.resolve().is_relative_to((ROOT / "data/raw").resolve()):
            raise ValueError("Snapshot directory must be inside data/raw")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        digest = hashlib.sha256(payload).hexdigest()
        suffix = "pdf" if kind == "cda_pdf" else "html" if kind == "html_report" else "json" if kind == "archive_json" else "bin"
        path = self.directory / f"{digest}.{suffix}"
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(payload); temporary.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing archive snapshot hash mismatch")
        self.artifacts.append(SimpleNamespace(relative_path=str(path.relative_to(ROOT)),
            sha256=digest, byte_size=len(payload), source_url=source_url, kind=kind, metadata_=metadata or {}))
        manifest = {"database_registered": False, "created_at": datetime.now(timezone.utc).isoformat(),
                    "artifacts": [vars(artifact) for artifact in self.artifacts]}
        temporary = self.directory / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)); temporary.replace(self.directory / "manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--reuse-cache", type=Path)
    parser.add_argument("--replay-manifest", type=Path)
    args = parser.parse_args()
    output = ROOT / "data/current/kh"; output.mkdir(parents=True, exist_ok=True)
    if args.replay_manifest:
        content = json.loads(args.replay_manifest.read_text())
        artifacts = [SimpleNamespace(**artifact) for artifact in content["artifacts"]]
        manifest_path = args.replay_manifest.resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        writer = FileSnapshotWriter(args.raw_directory or ROOT / "data/raw/kh/camewarn-history" / stamp)

        class SnapshotCrawler(CambodiaCamEWARNCrawler):
            def get(self, url, **kwargs):
                cached = args.reuse_cache / Path(urlparse(url).path).name if args.reuse_cache else None
                if cached and cached.is_file():
                    import requests
                    response = requests.Response(); response.status_code = 200; response.url = url
                    response._content = cached.read_bytes()
                    response.headers["X-GIDS-Retrieval-Mode"] = "cached_snapshot"
                    return response
                return super().get(url, **kwargs)

        asyncio.run(collect(writer, start_year=args.start_year, end_year=args.end_year, crawler_type=SnapshotCrawler))
        artifacts = writer.artifacts
        manifest_path = writer.directory / "manifest.json"
    print(f"Captured {len(artifacts)} artifacts; organizing {manifest_path}", flush=True)
    result = organize(artifacts, updater=CambodiaCamEWARNWeeklyUpdater(), start_year=args.start_year,
        end_year=args.end_year, parse_cache_directory=ROOT / "data/cache/kh/camewarn-parses")
    with result.source_csv.open("w", newline="", encoding="utf-8") as stream:
        csv_writer = csv.DictWriter(stream, fieldnames=list(result.rows[0]))
        csv_writer.writeheader(); csv_writer.writerows(result.rows)
    result.source_audit["raw_manifest"] = str(manifest_path.relative_to(ROOT))
    result.source_audit["database_registered"] = False
    (output / "source_audit.json").write_text(json.dumps(result.source_audit, ensure_ascii=False, indent=2))
    print("\n".join(result.script_logs), flush=True)
    print(f"CSV: {result.source_csv}; raw artifacts await database registration", flush=True)


if __name__ == "__main__":
    main()
