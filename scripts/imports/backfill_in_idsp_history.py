#!/usr/bin/env python3
"""Capture every official IDSP historical PDF; never write database facts.

The URL cache is resumable. Each invocation publishes new immutable, hash-
verified per-year manifests compatible with import_surveillance_archive.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.crawlers.in_idsp import DIRECTORY_URL, IndiaIDSPCrawler, discover_reports
from src.data.crawlers.za_cache import NICDReportCache
from src.services.ingestion.artifacts import _save_atomic


def save_original(directory, payload, *, source_url, kind, metadata):
    digest = hashlib.sha256(payload).hexdigest()
    suffix = {"cda_pdf": "pdf", "html_report": "html", "archive_json": "json"}[kind]
    target = directory / f"{kind}-{digest}.{suffix}"
    if not target.exists():
        _save_atomic(target, payload, digest)
    elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("Immutable historical original failed integrity check")
    return {"relative_path": target.relative_to(ROOT).as_posix(), "source_url": source_url,
            "kind": kind, "byte_size": len(payload), "sha256": digest, "metadata_": metadata}


def publish_manifest(directory, *, directory_artifact, reports, artifacts, failures,
                     available_count, label):
    years = [r["year"] for r in reports]
    plan = {"reports": reports, "available_reports": available_count,
            "omitted_by_limit": available_count - len(reports), "max_reports": len(reports),
            "requested_start_year": min(years), "requested_end_year": max(years),
            "failed_downloads": failures, "discovery_url": DIRECTORY_URL,
            "collection_mode": "all_discovered_history" if available_count == len(reports) else "historical_checkpoint"}
    plan_artifact = save_original(directory, json.dumps(plan, sort_keys=True).encode(),
        source_url=DIRECTORY_URL, kind="archive_json", metadata={"endpoint": "plan"})
    records = [directory_artifact, *artifacts, plan_artifact]
    document = {"country": "IN", "source": "in_idsp_weekly", "public_release_enabled": False,
                "created_at": datetime.now(timezone.utc).isoformat(), "artifacts": records}
    path = directory / f"manifest-{label}.json"
    with path.open("x") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"stage": "manifest", "label": label, "manifest": str(path),
        "expected_pdfs": len(reports), "captured_pdfs": len(artifacts), "failures": len(failures)}), flush=True)
    return str(path)


def capture_history(*, start_year=2009, end_year=2026, workers=4, cache_ttl_hours=87600):
    if not 1 <= workers <= 8:
        raise ValueError("Use 1–8 source request workers")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "data/raw/in" / f"idsp-history-{stamp}"
    directory.mkdir(parents=True)
    with IndiaIDSPCrawler().session as session:
        response = session.get(DIRECTORY_URL, timeout=45)
        response.raise_for_status()
    directory_artifact = save_original(directory, response.content, source_url=response.url,
        kind="html_report", metadata={"endpoint": "directory", "retrieved_at": datetime.now(timezone.utc).isoformat()})
    reports = discover_reports(response.content, start_year=start_year, end_year=end_year)
    reports.reverse()
    state = threading.local()
    sessions, sessions_lock = [], threading.Lock()

    def fetch(report):
        if not hasattr(state, "crawler"):
            state.crawler = IndiaIDSPCrawler(max_retries=1)
            state.cache = NICDReportCache(ROOT / "data/cache/in/idsp_history_transport")
            with sessions_lock:
                sessions.append(state.crawler.session)
        try:
            payload, receipt = state.cache.get(state.crawler, report["pdf_url"], ttl_hours=cache_ttl_hours)
            if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                raise ValueError("Incomplete official PDF response")
            artifact = save_original(directory, payload, source_url=report["pdf_url"], kind="cda_pdf",
                metadata={"endpoint": "pdf", "report": report, "retrieved_at": receipt["retrieved_at"],
                          "revalidated_at": receipt["revalidated_at"], "cache_status": receipt["cache_status"]})
            return report, artifact, None
        except Exception as exc:
            return report, None, {**report, "stage": "pdf", "error": f"{type(exc).__name__}: {exc}"}

    artifacts, failures, manifests = [], [], []
    checkpoint = False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for year in sorted({r["year"] for r in reports}, reverse=True):
                yearly = [report for report in reports if report["year"] == year]
                year_artifacts, year_failures = [], []
                futures = [executor.submit(fetch, report) for report in yearly]
                for future in concurrent.futures.as_completed(futures):
                    report, artifact, failure = future.result()
                    if artifact:
                        year_artifacts.append(artifact)
                        if not checkpoint:
                            manifests.append(publish_manifest(directory, directory_artifact=directory_artifact,
                                reports=[report], artifacts=[artifact], failures=[], available_count=len(reports), label="initial"))
                            checkpoint = True
                    else:
                        year_failures.append(failure)
                    print(json.dumps({"stage": "download", "year": year, "week": report["week"],
                        "status": "captured" if artifact else "failed", "complete_in_year": len(year_artifacts) + len(year_failures),
                        "total_in_year": len(yearly), "error": failure and failure["error"]}), flush=True)
                artifacts.extend(year_artifacts)
                failures.extend(year_failures)
                manifests.append(publish_manifest(directory, directory_artifact=directory_artifact, reports=yearly,
                    artifacts=year_artifacts, failures=year_failures, available_count=len(yearly), label=str(year)))
    finally:
        for session in sessions:
            session.close()
    combined = publish_manifest(directory, directory_artifact=directory_artifact, reports=reports,
        artifacts=artifacts, failures=failures, available_count=len(reports), label="all-history")
    receipt = {"source": "in_idsp_weekly", "manifest": combined, "year_manifests": manifests,
        "discovered_reports": len(reports), "captured_reports": len(artifacts), "failed_downloads": failures,
        "years": dict(Counter(r["year"] for r in reports)), "completed_at": datetime.now(timezone.utc).isoformat()}
    receipt_path = directory / "capture-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stage": "complete", "receipt": str(receipt_path), **{k:v for k,v in receipt.items() if k not in ("year_manifests", "failed_downloads")}}), flush=True)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2009)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cache-ttl-hours", type=float, default=87600)
    args = parser.parse_args()
    capture_history(**vars(args))
