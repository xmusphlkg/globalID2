#!/usr/bin/env python3
"""Reconcile the complete INS microdata archive with its official catalogue."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://portalsivigila.ins.gov.co"


def local_path(root, relative):
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    snapshot = local_path(root, manifest["catalogue_snapshot"])
    evidence = json.loads((snapshot / "manifest.json").read_text())
    items, errors, checksums = [], [], {}
    for artifact in evidence["artifacts"]:
        path = local_path(root, artifact["path"])
        if path.stat().st_size != artifact["byte_size"] or digest(path) != artifact["sha256"]:
            raise ValueError("Official catalogue snapshot failed integrity verification")
        checksums[artifact["path"]] = artifact["sha256"]
        if path.suffix == ".json":
            items.extend(json.loads(path.read_text())["d"]["results"])
    expected = {urljoin(BASE, item["FileRef"]): item for item in items}
    records = {record["source_url"]: record for record in manifest["files"]}
    if len(items) != len(expected) or len(records) != len(manifest["files"]):
        errors.append({"error": "duplicate_source_url"})
    if manifest["status"] != "complete" or manifest.get("errors"):
        errors.append({"error": "download_run_incomplete"})
    for url in sorted(expected.keys() - records.keys()):
        errors.append({"source_url": url, "error": "missing_file_record"})
    verified = []
    for url, item in expected.items():
        record = records.get(url)
        if record is None:
            continue
        try:
            path = local_path(root, record["path"])
            if (record["year"] != int(item["A_x00f1_o"])
                    or record["event_code"] != str(item["CodigoEvento"])
                    or record["source_modified"] != item["Modified"]):
                raise ValueError("source_metadata_mismatch")
            size = path.stat().st_size
            if size != int(item["File"]["Length"]) or size != record["byte_size"]:
                raise ValueError("byte_size_mismatch")
            if digest(path) != record["sha256"]:
                raise ValueError("sha256_mismatch")
            verified.append(record)
            checksums[record["path"]] = record["sha256"]
        except (OSError, ValueError) as exc:
            errors.append({"source_url": url, "error": str(exc)})
    by_year = Counter(record["year"] for record in verified)
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "status": "complete_verified" if not errors else "incomplete",
              "catalogue_snapshot": manifest["catalogue_snapshot"],
              "expected_files": len(expected), "verified_files": len(verified),
              "expected_bytes": sum(int(item["File"]["Length"]) for item in items),
              "verified_bytes": sum(record["byte_size"] for record in verified),
              "files_by_year": dict(sorted(by_year.items())), "errors": errors}
    (root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if errors:
        return report
    fields = ["year", "event_code", "event_name", "source_filename", "path", "byte_size", "sha256", "source_url", "source_modified", "captured_at"]
    with (root / "inventory.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in sorted(verified, key=lambda record: (record["year"], record["event_code"])):
            writer.writerow({**record, "source_filename": Path(urlsplit(record["source_url"]).path).name})
    (root / "SHA256SUMS").write_text("".join(f"{checksum}  {path}\n" for path, checksum in sorted(checksums.items())))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/raw/co/sivigila-microdatos")
    report = verify(parser.parse_args().output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete_verified" else 1


if __name__ == "__main__":
    sys.exit(main())
