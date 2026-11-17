#!/usr/bin/env python3
"""Capture every linked Pakistan NIH IDSR original into a resumable raw archive.

No database writes. The resulting manifest can be passed to
import_surveillance_archive.py --country PK --manifest ... --start-year 2021.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.pk_idsr import (
    INDEX_URL, PakistanNIHIDSRCrawler, discover_reports,
)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def save_artifact(directory, payload, *, source_url, kind, metadata):
    digest = hashlib.sha256(payload).hexdigest()
    extension = ".pdf" if kind == "cda_pdf" else ".html" if kind == "html_report" else ".json"
    path = directory / (digest + extension)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Corrupt archive artifact: {path}")
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
    return {"relative_path": path.relative_to(ROOT).as_posix(), "sha256": digest,
            "byte_size": len(payload), "source_url": source_url, "kind": kind,
            "metadata_": metadata}


def capture(directory, *, start_year=2021, end_year=None, workers=4, refresh_index=False):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "data/raw/pk"):
        raise ValueError("Archive must be under data/raw/pk")
    directory.mkdir(parents=True, exist_ok=True)
    index_receipt = directory / "index_receipt.json"
    if index_receipt.exists() and not refresh_index:
        index = json.loads(index_receipt.read_text())
        payload = (ROOT / index["relative_path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != index["sha256"]:
            raise ValueError("Captured NIH index integrity failure")
    else:
        crawler = PakistanNIHIDSRCrawler()
        try:
            payload = crawler.get(INDEX_URL).content
        finally:
            crawler.session.close()
        index = save_artifact(directory, payload, source_url=INDEX_URL, kind="html_report",
                              metadata={"endpoint": "index", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        atomic_json(index_receipt, index)
    targets = discover_reports(payload, start_year=start_year, end_year=end_year, max_reports=None)
    print(json.dumps({"stage": "discovered", "reports": len(targets), "years": sorted({r['year'] for r in targets})}), flush=True)
    receipt_directory = directory / "download_receipts"
    receipt_directory.mkdir(exist_ok=True)
    failures = []
    captured = []

    def download(report):
        key = hashlib.sha256(report["pdf_url"].encode()).hexdigest()
        receipt_path = receipt_directory / (key + ".json")
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            original = (ROOT / receipt["relative_path"]).read_bytes()
            if len(original) != receipt["byte_size"] or hashlib.sha256(original).hexdigest() != receipt["sha256"]:
                raise ValueError("Cached original PDF integrity failure")
            if receipt["source_url"] == report["pdf_url"]:
                return receipt
        crawler = PakistanNIHIDSRCrawler(max_retries=2)
        try:
            response = crawler.get(report["pdf_url"])
            original = response.content
            if not original.startswith(b"%PDF") or b"%%EOF" not in original[-65536:]:
                raise ValueError("Incomplete or invalid NIH PDF")
            receipt = save_artifact(directory, original, source_url=report["pdf_url"], kind="cda_pdf",
                                    metadata={"endpoint": "pdf", "report": report,
                                              "retrieved_at": datetime.now(timezone.utc).isoformat()})
            atomic_json(receipt_path, receipt)
            return receipt
        finally:
            crawler.session.close()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download, report): report for report in targets}
        for index_number, future in enumerate(as_completed(futures), 1):
            report = futures[future]
            try:
                captured.append(future.result())
            except Exception as exc:
                failures.append({**report, "stage": "download", "error": str(exc)})
            atomic_json(directory / "capture_progress.json", {"targets": len(targets), "completed": index_number,
                "captured": len(captured), "failures": failures})
            if index_number % 10 == 0 or index_number == len(targets):
                print(json.dumps({"stage": "capture", "completed": index_number, "total": len(targets),
                    "captured": len(captured), "failed": len(failures)}), flush=True)
    captured.sort(key=lambda record: record["source_url"])
    plan = {"targets": targets, "selected": targets,
            "manifest": [{"url": record["source_url"], "sha256": record["sha256"]} for record in captured],
            "failed_downloads": failures, "requested_start_year": int(start_year),
            "requested_end_year": end_year, "requested_weeks": None, "max_reports": None,
            "discovery_url": INDEX_URL, "omitted_by_limit": 0}
    plan_artifact = save_artifact(directory, json.dumps(plan, sort_keys=True).encode(), source_url=INDEX_URL,
                                  kind="archive_json", metadata={"endpoint": "plan"})
    manifest_path = directory / "manifest.json"
    atomic_json(manifest_path, {"country": "PK", "source": "pk_nih_idsr_weekly",
        "created_at": datetime.now(timezone.utc).isoformat(), "artifacts": [index, *captured, plan_artifact]})
    print(json.dumps({"stage": "complete", "manifest": str(manifest_path), "captured": len(captured),
                      "failed": len(failures)}), flush=True)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "data/raw/pk/nih_idsr_history_20261117")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--refresh-index", action="store_true")
    arguments = parser.parse_args()
    capture(arguments.directory, start_year=arguments.start_year, end_year=arguments.end_year,
            workers=arguments.workers, refresh_index=arguments.refresh_index)


if __name__ == "__main__":
    main()
