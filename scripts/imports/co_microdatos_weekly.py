#!/usr/bin/env python3
"""Build audited weekly record aggregates from the local INS microdata archive.

No database writes. Resume per workbook using its original hash and full source
metadata. Collective/changed/invalid workbooks are listed as quarantines.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.collect_co_microdatos import write_json
from src.data.crawlers.co_microdatos import (
    DEFAULT_SCOPE,
    PARSER_VERSION,
    aggregate_workbook,
    archive_records,
    sha256_file,
)


def one(root, record, cache):
    path = root / record["path"]
    identity = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    cached_path = cache / f"{identity}-{PARSER_VERSION}.json"
    # Always check original bytes, even when reusing a previous aggregate.
    if path.stat().st_size != record["byte_size"] or sha256_file(path) != record["sha256"]:
        raise ValueError("Microdata original failed size/SHA-256 verification")
    if cached_path.exists():
        return json.loads(cached_path.read_bytes())
    result = aggregate_workbook(path, record)
    write_json(cached_path, result)
    return result


def build(root, output, *, start_year=2025, end_year=2025, events=None, workers=4):
    records = archive_records(root, start_year=start_year, end_year=end_year, events=events)
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "workbooks"
    cache.mkdir(exist_ok=True)
    rows, audits = [], []
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(one, root, record, cache): record for record in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - retain a per-workbook failure without losing other audit results
                result = {"rows": [], "audit": {
                    "year": record["year"], "event_code": record["event_code"],
                    "source_url": record["source_url"], "sha256": record["sha256"],
                    "status": "failed", "error_type": type(exc).__name__}}
            rows.extend(result["rows"])
            audits.append(result["audit"])
            print(json.dumps({"event": record["event_code"], "year": record["year"],
                              "status": result["audit"]["status"], "completed": len(audits),
                              "total": len(records)}, ensure_ascii=False), flush=True)
    rows.sort(key=lambda r: (r["Date"], r["SourceDiseaseCode"], r["Dimensions"]))
    target = output / "weekly_notification_records.csv"
    temporary = target.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        fields = sorted({field for row in rows for field in row}) or ["Date", "Cases"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(target)
    quarantined = [audit for audit in audits if audit["status"] != "aggregated_source_records"]
    result = {
        "source": DEFAULT_SCOPE, "parser_version": PARSER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "aggregated_with_quarantines" if quarantined else "aggregated_source_records",
        "requested_years": [start_year, end_year], "selected_events": sorted(events) if events else None,
        "archive_manifest_sha256": sha256_file(root / "manifest.json"),
        "selected_workbooks": len(records), "aggregated_workbooks": len(audits) - len(quarantined),
        "quarantined_workbooks": len(quarantined), "weekly_strata": len(rows),
        "aggregated_records": sum(int(row["Cases"]) for row in rows),
        "source_csv": str(target), "source_csv_sha256": sha256_file(target),
        "official_national_weekly_table": False, "onset_incidence_derived": False,
        "public_release_enabled": False, "database_imported": False,
        "workbooks": sorted(audits, key=lambda a: (a["year"], a["event_code"])),
        "coverage_note": "Counts of archived individual notifications by source year/week and raw case "
                         "status, adjustment and occurrence country. Absent weeks are unknown. Collective "
                         "forms are quarantined. Not interchangeable with historical municipality counts "
                         "or BES cumulative counts; no 2026 workbook is implied.",
    }
    write_json(output / "audit.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "data/raw/co/sivigila-microdatos")
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/co/microdatos-weekly")
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--event", action="append", help="Optional event code; repeat to select several")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    if not 2007 <= args.start_year <= args.end_year < datetime.now(timezone.utc).year:
        parser.error("Only completed statistical years, from 2007 onward, are supported")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "aggregate.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = build(args.archive.resolve(), args.output.resolve(), start_year=args.start_year,
                       end_year=args.end_year, events=set(args.event) if args.event else None,
                       workers=args.workers)
    print(json.dumps({key: value for key, value in result.items() if key != "workbooks"}, ensure_ascii=False))
    return 2 if any(a["status"] == "failed" for a in result["workbooks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
