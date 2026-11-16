#!/usr/bin/env python3
"""Resume bounded INS SIVIGILA source pages and import each verified batch."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import json
from pathlib import Path
import signal
import subprocess
import sys

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import String

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from scripts.imports.import_surveillance_archive import load_archive_manifest
from src.core.database import get_db, dispose_database
from src.data.crawlers.co import (COUNT_URL, HISTORY_START_YEAR, METADATA_URL,
                                  ColombiaINSSivigilaCrawler, can_skip_import,
                                  parse_metadata)
from src.data.processors.co import ColombiaINSSivigilaUpdater
from src.services.ingestion.countries.co import collect, organize


def atomic_json(path: Path, document: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2))
    temporary.replace(path)


def live_year_counts(crawler, *, start_year, end_year):
    response = crawler.get(COUNT_URL, params={"$select": "ano,count(*) as n",
                                               "$group": "ano", "$order": "ano"})
    counts = {int(item["ano"]): int(item["n"]) for item in response.json()}
    selected = {year: counts[year] for year in range(start_year, end_year + 1)
                if counts.get(year, 0) > 0}
    if not selected:
        raise ValueError("Official INS dataset has no rows in requested years")
    return counts, selected


def capture_batch(*, year, offset, page_limit, version, year_total, previous, run_dir):
    if previous.get("manifest"):
        manifest = ROOT / previous["manifest"]
        artifacts = load_archive_manifest(manifest, "CO")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = run_dir / str(year) / f"offset-{offset}" / stamp
        writer = FileSnapshotWriter(directory)
        asyncio.run(collect(writer, start_year=year, end_year=year,
                            page_offset=offset, page_limit=page_limit))
        manifest = writer.directory / "manifest.json"
        artifacts = writer.artifacts
    result = organize(artifacts, updater=ColombiaINSSivigilaUpdater(),
                      start_year=year, end_year=year, page_offset=offset)
    if result.source_audit["dataset_version"] != version:
        raise ValueError("INS source changed between historical pages; start a new version run")
    if result.source_audit["year_source_rows"] != year_total:
        raise ValueError("INS annual source count changed between historical pages")
    expected_rows = min(page_limit, year_total - offset)
    if len(result.rows) != expected_rows:
        raise ValueError("Captured CO page does not match frozen year count")
    return manifest, result.rows


def merge_year_states(state, run_dir, identity):
    for year in identity["year_counts"]:
        path = run_dir / year / "year-state.json"
        if not path.exists():
            continue
        saved = json.loads(path.read_text())
        if saved["identity"] != {"dataset_version": identity["dataset_version"],
                                 "year": int(year), "year_rows": identity["year_counts"][year],
                                 "page_limit": identity["page_limit"]}:
            raise ValueError(f"CO year {year} state belongs to another source version")
        for key, batch in saved["batches"].items():
            prior = state["batches"].get(key, {})
            if batch.get("applied") or not prior.get("applied"):
                state["batches"][key] = batch


def stored_row_matches_source(stored, source):
    raw = stored.raw_data or {}
    return (str(stored.time.date() if hasattr(stored.time, "date") else stored.time) == source["Date"]
            and stored.geography_key == source["GeographyKey"]
            and Decimal(str(stored.value)) == Decimal(source["Cases"])
            and all(raw.get(key) == source[key] for key in (
                "SourceRecordID", "SourceDiseaseCode", "Date", "GeographyKey",
                "Cases", "Dimensions", "RawSHA256")))


def next_ready_future(futures):
    ready = next((item for item in futures if item.done()), None)
    return ready if ready is not None else next(as_completed(tuple(futures)))


async def reconcile_database_page(rows, manifest):
    """Prove a committed but unreceipted page by its source row IDs and values."""
    expected = {row["SourceRecordID"]: row for row in rows}
    if len(expected) != len(rows):
        raise ValueError("Duplicate Socrata IDs in captured page")
    query = text("""
        SELECT obs.time, obs.geography_key, obs.value, obs.raw_data
        FROM disease_series_observations obs
        JOIN disease_surveillance_series series ON series.series_code=obs.series_code
        WHERE series.country_code='CO' AND series.source_system='SRC_CO_INS_SIVIGILA'
          AND obs.raw_data->>'SourceRecordID' = ANY(:source_ids)
    """).bindparams(bindparam("source_ids", type_=ARRAY(String)))
    task_query = text("""
        SELECT task_uuid FROM tasks
        WHERE input_data->>'archive_manifest' = :manifest
        ORDER BY started_at DESC LIMIT 1
    """)
    try:
        async with get_db() as db:
            found = 0
            for stored in (await db.execute(query, {"source_ids": list(expected)})).all():
                source_id = (stored.raw_data or {}).get("SourceRecordID")
                source = expected.pop(source_id, None)
                if source is None:
                    return None
                if not stored_row_matches_source(stored, source):
                    return None
                found += 1
            if expected or found != len(rows):
                return None
            return (await db.execute(task_query, {"manifest": str(manifest.resolve())})).scalar()
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--page-limit", type=int, default=10_000)
    parser.add_argument("--capture-workers", type=int, default=3,
                        help="Concurrent source years; database imports remain single-writer")
    parser.add_argument("--max-batches", type=int, help="Bound this invocation; rerun to resume")
    parser.add_argument("--check-only", action="store_true",
                        help="Report source-version and year-count changes without capture/import")
    parser.add_argument("--apply", action="store_true", help="Import through normal staged runner")
    args = parser.parse_args()
    end_year = args.end_year or args.start_year
    if (args.start_year < HISTORY_START_YEAR or end_year < args.start_year
            or not 1 <= args.page_limit <= 20_000 or not 1 <= args.capture_workers <= 4
            or (args.max_batches is not None and args.max_batches < 1)):
        parser.error("Invalid source year range or page limit")
    controller_lock = None
    if not args.check_only:
        lock_path = ROOT / "data/current/co/history-controller.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        controller_lock = lock_path.open("a")
        fcntl.flock(controller_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop_requested = False
    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGUSR1, request_stop)
    crawler = ColombiaINSSivigilaCrawler()
    try:
        version = parse_metadata(crawler.get(METADATA_URL).content)
        all_counts, counts = live_year_counts(crawler, start_year=args.start_year, end_year=end_year)
    finally:
        crawler.session.close()
    version_key = f"{version['rows_updated_at']}-{version['view_last_modified']}"
    run_dir = ROOT / "data/raw/co/sivigila-history" / version_key
    state_path = run_dir / "state.json"
    identity = {"dataset_version": version,
                "year_counts": {str(year): total for year, total in counts.items()},
                "start_year": args.start_year, "end_year": end_year,
                "page_limit": args.page_limit}
    summary_path = ROOT / "data/current/co/history-state.json"
    if args.check_only:
        prior = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        prior_identity = prior.get("identity", {})
        acceptance_path = ROOT / "data/current/co/database_acceptance.json"
        acceptance = json.loads(acceptance_path.read_text()) if acceptance_path.exists() else {}
        version_changed = bool(prior_identity) and prior_identity.get("dataset_version") != version
        published_counts = {str(year): total for year, total in all_counts.items() if total > 0}
        counts_changed = bool(prior_identity) and prior_identity.get("year_counts") != published_counts
        can_skip = can_skip_import(version, published_counts, prior, acceptance)
        print(json.dumps({"source": "co_ins_sivigila_historical", "dataset_version": version,
                          "year_counts": published_counts,
                          "version_changed": version_changed,
                          "year_counts_changed": counts_changed,
                          "last_import_status": prior.get("status"),
                          "can_skip_import": can_skip}, ensure_ascii=False))
        return
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("Existing CO history state uses another range or page size")
    else:
        state = {"identity": identity, "batches": {}, "status": "running"}
    merge_year_states(state, run_dir, identity)
    state["status"] = "running"
    def save():
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["completed_batches"] = sum(bool(v.get("applied")) for v in state["batches"].values())
        state["completed_rows"] = sum(v.get("rows", 0) for v in state["batches"].values()
                                      if v.get("applied") or not args.apply)
        state["expected_rows"] = sum(counts.values())
        for year, year_total in counts.items():
            year_batches = {key: batch for key, batch in state["batches"].items()
                            if batch["year"] == year}
            year_rows = sum(batch["rows"] for batch in year_batches.values()
                            if batch.get("applied"))
            atomic_json(run_dir / str(year) / "year-state.json", {
                "identity": {"dataset_version": version, "year": year,
                             "year_rows": year_total, "page_limit": args.page_limit},
                "batches": year_batches, "completed_rows": year_rows,
                "status": "complete" if year_rows == year_total else "running",
                "updated_at": state["updated_at"]})
        atomic_json(state_path, state)
        atomic_json(summary_path, {**state, "state_path": str(state_path.relative_to(ROOT)),
                                   "public_release_enabled": False})

    batches_this_run = 0
    save()
    pending = deque((year, [offset for offset in range(0, total, args.page_limit)
                            if not state["batches"].get(f"{year}:{offset}", {}).get("applied")
                            and (args.apply or not state["batches"].get(f"{year}:{offset}", {}).get("organized"))])
                    for year, total in counts.items())
    pending = deque((year, iter(offsets)) for year, offsets in pending if offsets)
    active = {}
    futures = {}
    capture_limit = min(args.capture_workers, args.max_batches or args.capture_workers)
    with ThreadPoolExecutor(max_workers=capture_limit) as pool:
        def submit_next(year):
            offset = next(active[year], None)
            if offset is None:
                del active[year]
                return False
            future = pool.submit(capture_batch, year=year, offset=offset,
                page_limit=args.page_limit, version=version, year_total=counts[year],
                previous=state["batches"].get(f"{year}:{offset}", {}), run_dir=run_dir)
            futures[future] = (year, offset)
            return True

        def fill_workers():
            while len(active) < capture_limit and pending:
                year, offsets = pending.popleft()
                active[year] = offsets
                submit_next(year)

        fill_workers()
        while futures:
            future = next_ready_future(futures)
            year, offset = futures.pop(future)
            manifest, source_rows = future.result()
            rows = len(source_rows)
            key = f"{year}:{offset}"
            previous = state["batches"].get(key, {})
            state["batches"][key] = {"year": year, "offset": offset, "rows": rows,
                                      "manifest": str(manifest.relative_to(ROOT)),
                                      "organized": True, "applied": False}
            save()
            if args.max_batches is None or batches_this_run + len(futures) + 1 < args.max_batches:
                submit_next(year)
                fill_workers()
            if args.apply:
                receipt_path = manifest.parent / "database_import_receipt.json"
                log = manifest.parent / "database_import.log"
                receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
                if receipt is None and previous.get("organized"):
                    recovered_task = asyncio.run(reconcile_database_page(source_rows, manifest))
                    if recovered_task:
                        receipt = {"source_manifest": str(manifest.resolve()),
                                   "records": rows, "task_uuid": recovered_task,
                                   "recovery": "database_identity_value_verified"}
                        atomic_json(receipt_path, receipt)
                if receipt is None:
                    with log.open("a") as stream:
                        subprocess.run([sys.executable,
                            str(ROOT / "scripts/imports/import_surveillance_archive.py"),
                            "--country", "CO", "--manifest", str(manifest),
                            "--start-year", str(year), "--apply",
                            "--receipt-path", str(receipt_path)],
                            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
                    receipt = json.loads(receipt_path.read_text())
                if receipt["source_manifest"] != str(manifest.resolve()):
                    raise ValueError("CO database receipt belongs to another archive page")
                if receipt["records"] != rows:
                    if not asyncio.run(reconcile_database_page(source_rows, manifest)):
                        raise ValueError("CO database receipt and page observations disagree")
                    receipt["reconciled_rows"] = rows
                    receipt["recovery"] = "database_identity_value_verified"
                    atomic_json(receipt_path, receipt)
                atomic_json(ROOT / "data/current/co/years" / f"{year}-receipt.json", receipt)
                state["batches"][key]["applied"] = True
                state["batches"][key]["task_uuid"] = receipt["task_uuid"]
            batches_this_run += 1
            save()
            print(json.dumps({"year": year, "offset": offset, "rows": rows,
                              "applied": args.apply, "completed_rows": state["completed_rows"],
                              "expected_rows": state["expected_rows"]}), flush=True)
            if args.max_batches is not None and batches_this_run >= args.max_batches:
                state["status"] = "paused_at_batch_limit"; save(); return
            if stop_requested:
                state["status"] = "paused_by_signal"; save(); return
    state["status"] = "complete" if args.apply else "organized_only"
    save()


if __name__ == "__main__":
    main()
