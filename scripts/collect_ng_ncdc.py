#!/usr/bin/env python3
"""Capture every linked NCDC archive report, or organize a verified capture.

This does not modify the database or publish country snapshots. --resume is an
explicit historical recovery mode, not proof of a newly fetched live snapshot.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

from src.data.crawlers.ng import SITREP_URL, WEEKLY_URL, discover_categories, discover_reports, discover_weekly_pages
from src.data.processors.ng import NigeriaNCDCUpdater, write_review_export
from src.services.ingestion.countries.ng import organize_documents


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def capture_archive(directory, *, resume=False, workers=4):
    directory.mkdir(parents=True, exist_ok=True)

    def fetch(url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = directory / (key + (".pdf" if ".pdf" in url else ".html"))
        receipt_path = directory / (key + ".json")
        if resume and path.exists() and receipt_path.exists():
            payload, receipt = path.read_bytes(), json.loads(receipt_path.read_text())
            if len(payload) == receipt["bytes"] and hashlib.sha256(payload).hexdigest() == receipt["sha256"]:
                return payload, receipt
        error = None
        for attempt in range(3):
            try:
                # NCDC occasionally returns an empty 200 on a reused HTTP
                # connection. Use ordinary fresh connections and retry; no
                # private API, credentials or challenge bypass is involved.
                response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; GlobalID/2.0)", "Connection": "close"}, timeout=(15, 45))
                response.raise_for_status()
                payload = response.content
                if not payload or (".pdf" in url and not payload.startswith(b"%PDF")):
                    raise ValueError("Empty/non-PDF source response")
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(payload)
                temporary.replace(path)
                receipt = {"url": url, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "path": str(path), "retrieved_at": datetime.now(timezone.utc).isoformat()}
                _write_json(receipt_path, receipt)
                return payload, receipt
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        raise ValueError(f"NCDC fetch failed after three attempts: {error}")

    payload, _ = fetch(WEEKLY_URL)
    weekly_pages = discover_weekly_pages(payload)
    payload, _ = fetch(SITREP_URL)
    categories = discover_categories(payload)
    listings = [(url, "weekly") for url in weekly_pages] + [(url, "sitrep") for url in categories]
    print(f"[discovery] {len(weekly_pages)} weekly routes; {len(categories)} disease categories", flush=True)

    def discover(item):
        url, kind = item
        try:
            payload, _ = fetch(url)
            reports = discover_reports(payload, listing_url=url, kind=kind)
            return {"url": url, "kind": kind, "report_count": len(reports)}, reports
        except Exception as exc:
            return {"url": url, "kind": kind, "error": str(exc)}, []

    catalogue_audit, reports = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, (audit, found) in enumerate(executor.map(discover, listings), 1):
            catalogue_audit.append(audit)
            reports.extend(found)
            if index % 40 == 0:
                print(f"[catalogues] {index}/{len(listings)}", flush=True)
    reports = list({r["url"]: r for r in reports}.values())
    _write_json(directory / "catalogue_audit.json", catalogue_audit)
    _write_json(directory / "discovered.json", reports)

    def download(report):
        try:
            _, receipt = fetch(report["url"])
            return report | receipt
        except Exception as exc:
            return report | {"error": str(exc)}

    captured = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, report in enumerate(executor.map(download, reports), 1):
            captured.append(report)
            if index % 25 == 0:
                _write_json(directory / "capture_manifest.json", captured)
                print(f"[capture] {index}/{len(reports)}; failed {sum(bool(r.get('error')) for r in captured)}", flush=True)
    _write_json(directory / "capture_manifest.json", captured)
    return captured, catalogue_audit


def export_artifacts(directory):
    reports = json.loads((directory / "capture_manifest.json").read_text())
    catalogue_path = directory / "catalogue_audit.json"
    catalogue_audit = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else []
    plan = {"reports": reports, "failed_downloads": [r for r in reports if r.get("error")] + [r for r in catalogue_audit if r.get("error")], "catalogue_audit": catalogue_audit, "mode": "explicit_historical_capture_recovery"}
    plan_path = directory / "organization_plan.json"
    _write_json(plan_path, plan)
    artifacts = []
    report_by_url = {r["url"]: r for r in reports if not r.get("error")}
    for receipt_path in sorted(directory.glob("*.json")):
        if len(receipt_path.stem) != 64:
            continue
        receipt = json.loads(receipt_path.read_text())
        path = Path(receipt["path"])
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != receipt["sha256"] or len(payload) != receipt["bytes"]:
            raise ValueError(f"Captured artifact integrity failed: {path}")
        url = receipt["url"]
        if path.suffix == ".pdf" and url not in report_by_url:
            continue
        artifacts.append({"kind": "ng_ncdc_pdf" if path.suffix == ".pdf" else "ng_ncdc_listing", "source_url": url, "relative_path": path.as_posix(), "sha256": receipt["sha256"], "byte_size": receipt["bytes"], "metadata_": {"report": report_by_url[url], "recovery": True, "retrieved_at": receipt.get("retrieved_at")} if path.suffix == ".pdf" else {"recovery": True}})
    payload = plan_path.read_bytes()
    artifacts.append({"kind": "ng_ncdc_plan", "source_url": WEEKLY_URL, "relative_path": plan_path.as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload), "metadata_": {"recovery": True}})
    _write_json(directory / "artifacts_manifest.json", artifacts)
    return reports, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data/raw/ng/ncdc_full_history"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--organize", action="store_true")
    options = parser.parse_args()
    if options.download:
        capture_archive(options.directory, resume=options.resume, workers=options.workers)
    reports, plan = export_artifacts(options.directory)
    print(f"[artifacts] {sum(not r.get('error') for r in reports)}/{len(reports)} report PDFs captured", flush=True)
    if options.organize:
        def documents():
            for report in reports:
                if report.get("error"):
                    continue
                payload = Path(report["path"]).read_bytes()
                if len(payload) != report["bytes"] or hashlib.sha256(payload).hexdigest() != report["sha256"]:
                    raise ValueError("NCDC captured source integrity failed")
                yield report, payload
        rows, audit = organize_documents(documents(), expected_reports=reports, failed_downloads=plan["failed_downloads"])
        fetched = NigeriaNCDCUpdater().prepare_rows(rows, source_audit=audit)
        write_review_export(fetched)
        print(f"[organized] {len(rows)} observations; parsed {audit['parsed_reports']}; unparsed {audit['unparsed_reports']}", flush=True)


if __name__ == "__main__":
    main()
