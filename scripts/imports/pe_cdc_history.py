#!/usr/bin/env python3
"""Resumable complete Peru CDC archive, with serial registered DB batches.

The full published directory is immutable per run. Completed PDF bytes are
archived before parsing; partial transfers remain resumable HTTP cache only.
Restart this command to resume unfinished downloads and failed DB batches.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data.crawlers.pe import (PeruCDCCrawler, DEFAULT_CACHE, HISTORY_START_YEAR,
    LISTING_URL, LISTING_FORM, discover_reports, PARSER_SHA256)
from src.data.processors.pe import PeruCDCWeeklyUpdater
from src.services.ingestion.countries.pe import organize
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from scripts.imports.import_surveillance_archive import load_archive_manifest


def atomic_json(path, document):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2)); temp.replace(path)


def cached_report(reference, directory):
    path = directory / (hashlib.sha256(reference["url"].encode()).hexdigest() + ".pdf")
    receipt_path = path.with_suffix(".json")
    if not path.exists() or not receipt_path.exists():
        return None
    try:
        receipt = json.loads(receipt_path.read_text()); payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != receipt["sha256"]:
            return None
        if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
            return None
        return payload, {"retrieval_mode": "cached_snapshot", **receipt}
    except (ValueError, KeyError, OSError):
        return None


def download_one(reference, directory):
    crawler = PeruCDCCrawler(cache_directory=directory)
    try:
        return crawler.download(reference)
    finally:
        crawler.session.close()


def persist_batch(run_directory, listing, targets, references, cache_directory, errors):
    digest = hashlib.sha256(json.dumps(sorted(r["url"] for r in references)).encode()).hexdigest()[:20]
    directory = run_directory / ("batch-" + digest)
    if (directory / "manifest.json").exists():
        existing = json.loads((directory / "manifest.json").read_text())
        if any(a.get("metadata_", {}).get("endpoint") == "collection_audit" for a in existing["artifacts"]):
            return directory / "manifest.json"
    writer = FileSnapshotWriter(directory)
    async def capture():
        await writer.write(listing, source_url=LISTING_URL, kind="html_report",
            metadata={"endpoint": "directory", "request_method": "POST", "request_form": LISTING_FORM})
        captured = []
        for reference in references:
            cached = cached_report(reference, cache_directory)
            if cached is None:
                raise ValueError("Completed cache changed before immutable snapshot")
            payload, receipt = cached
            await writer.write(payload, source_url=reference["url"], kind="cda_pdf",
                metadata={"endpoint": "bulletin", "reference": reference, "receipt": receipt})
            captured.append({"url": reference["url"], "sha256": hashlib.sha256(payload).hexdigest()})
        await writer.write(json.dumps({"targets": targets, "captured": captured, "errors": errors,
            "retrieved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False).encode(),
            source_url=LISTING_URL, kind="archive_json", metadata={"endpoint": "collection_audit"})
    asyncio.run(capture())
    return directory / "manifest.json"


def assert_loaded_parser_is_current():
    import src.data.crawlers.pe as parser
    if hashlib.sha256(Path(parser.__file__).read_bytes()).hexdigest() != PARSER_SHA256:
        raise RuntimeError("Peru parser source changed after controller startup; restart controller before CSV or DB import")


def organize_and_import(manifest, *, apply, output_directory, start_year, end_year):
    assert_loaded_parser_is_current()
    artifacts = load_archive_manifest(manifest, "PE")
    result = organize(artifacts, updater=PeruCDCWeeklyUpdater(), start_year=start_year,
        end_year=end_year, parse_cache_directory=ROOT / "data/cache/pe/bulletin-parses")
    atomic_json(manifest.parent / "source_audit.json", result.source_audit)
    if result.rows:
        # This controller owns the PE current CSV; DB importer writes its own
        # canonical current export synchronously, never alongside another batch.
        prior = {}
        csv_path = output_directory / "peru_cdc_weekly.csv"
        if csv_path.exists():
            with csv_path.open() as stream:
                for row in csv.DictReader(stream):
                    prior[(row["Date"], row["SourceDiseaseCode"], row["DefinitionVersion"])] = row
        for row in result.rows:
            prior[(row["Date"], row["SourceDiseaseCode"], row["DefinitionVersion"])] = row
        temp = csv_path.with_suffix(".csv.tmp")
        with temp.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result.rows[0])); writer.writeheader()
            writer.writerows(sorted(prior.values(), key=lambda r: (r["Date"], r["SourceDiseaseCode"], r["DefinitionVersion"])))
        if apply:
            log_path = manifest.parent / "database_import.log"
            with log_path.open("a") as log:
                subprocess.run([sys.executable, str(ROOT / "scripts/imports/import_surveillance_archive.py"),
                    "--country", "PE", "--manifest", str(manifest), "--start-year", str(start_year), "--apply"],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        temp.replace(csv_path)
    elif apply:
        # Empty numerical output must still register original source artifacts.
        # The standard importer safely records its empty organized result.
        with (manifest.parent / "database_import.log").open("a") as log:
            subprocess.run([sys.executable, str(ROOT / "scripts/imports/import_surveillance_archive.py"),
                "--country", "PE", "--manifest", str(manifest), "--start-year", str(start_year), "--apply"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(json.dumps({"batch": str(manifest), "rows": len(result.rows), "applied": apply}), flush=True)
    return len(result.rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--retry-rounds", type=int, default=3)
    parser.add_argument("--listing-file", type=Path)
    parser.add_argument("--cache-directory", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--cached-only", action="store_true")
    parser.add_argument("--supervise", action="store_true", help="Retry failed controller/import runs every 30 seconds")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or args.batch_size < 1:
        parser.error("workers must be 1–16 and batch-size positive")
    output = ROOT / "data/current/pe"; output.mkdir(parents=True, exist_ok=True)
    lock = (output / "history-controller.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = output / "history-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"batches": {}, "errors": {}, "attempts": {}}
    if args.listing_file:
        listing = args.listing_file.read_bytes()
    elif state.get("listing_path") and Path(state["listing_path"]).exists():
        listing = Path(state["listing_path"]).read_bytes()
    else:
        crawler = PeruCDCCrawler(cache_directory=args.cache_directory)
        try:
            _, listing = crawler.discover(start_year=args.start_year, end_year=args.end_year)
        finally:
            crawler.session.close()
    targets = discover_reports(listing, start_year=args.start_year, end_year=args.end_year)
    bound_identity = {"directory_sha256": hashlib.sha256(listing).hexdigest(),
        "start_year": args.start_year, "end_year": args.end_year}
    if state.get("identity") and state["identity"] != bound_identity:
        raise ValueError("Controller state is bound to another directory/range; preserve it and use a separate state directory")
    state["identity"] = bound_identity
    identity = hashlib.sha256(listing).hexdigest()[:16]
    run = ROOT / "data/raw/pe/cdc-history" / identity; run.mkdir(parents=True, exist_ok=True)
    listing_path = run / "full_listing.html"
    if not listing_path.exists():
        listing_path.write_bytes(listing)
    state.update({"listing_path": str(listing_path), "directory_sha256": hashlib.sha256(listing).hexdigest(),
        "published_reports": len(targets), "pid": os.getpid(), "status": "running", "apply": args.apply})
    def save():
        batch_reports = sum(len(b["urls"]) for b in state["batches"].values())
        captured = {url for b in state["batches"].values() for url in b["urls"]}
        state["archived_reports"] = len(captured)
        state["current_valid_native_metrics"] = sum(b.get("rows", 0) for b in state["batches"].values())
        state["updated_at"] = datetime.now(timezone.utc).isoformat(); atomic_json(state_path, state)
        atomic_json(output / "source_audit.json", {"status": "partial",
            "controller_status": state["status"], "controller_pid": state["pid"],
            "updated_at": state["updated_at"], "published_reports": len(targets),
            "archived_reports": len(captured), "outstanding_reports": len(targets) - len(captured),
            "batch_report_appearances": batch_reports,
            "organized_native_metrics": sum(b.get("rows", 0) for b in state["batches"].values()),
            "database_applied_batches": sum(bool(b.get("applied")) for b in state["batches"].values()),
            "batches": state["batches"], "download_errors": state["errors"],
            "public_release_enabled": False, "canonical_mappings_created": False,
            "coverage_note": "Complete official plan; unsupported scans/tables and outstanding transfers are not zero. See immutable batch source_audit.json for exact numerical coverage."})
    def handle(manifest, urls):
        key = str(manifest.relative_to(ROOT)); previous = state["batches"].get(key, {})
        if previous.get("applied") or (previous.get("organized") and not args.apply):
            return
        state["batches"][key] = {**previous, "urls": urls, "organized": False, "applied": False}; save()
        try:
            rows = organize_and_import(manifest, apply=args.apply, output_directory=output,
                start_year=args.start_year, end_year=args.end_year)
            state["batches"][key].update({"organized": True, "applied": args.apply, "rows": rows})
        except Exception as exc:
            state["batches"][key]["error"] = str(exc); save(); raise
        save()
    save()
    # Replay pending batches first after a crash or failed DB transaction.
    for key, batch in list(state["batches"].items()):
        handle(ROOT / key, batch["urls"])
    archived = {url for batch in state["batches"].values() for url in batch["urls"]}
    def checkpoint(references):
        if not references:
            return
        # Same-period official variants must meet in one organization call;
        # the organizer defers a solitary edition, so prior batches cannot
        # silently install one edition before its sibling is compared.
        keys = {(r["year"], r["week"]) for r in references}
        expanded = {r["url"]: r for r in references}
        for target in targets:
            if (target["year"], target["week"]) in keys and cached_report(target, args.cache_directory):
                expanded[target["url"]] = target
        references = list(expanded.values())
        manifest = persist_batch(run, listing, targets, references, args.cache_directory, list(state["errors"].values()))
        handle(manifest, [r["url"] for r in references]); archived.update(r["url"] for r in references)
        state["archived_reports"] = len(archived); save()
    existing = [r for r in targets if r["url"] not in archived and cached_report(r, args.cache_directory)]
    for offset in range(0, len(existing), args.batch_size):
        checkpoint(existing[offset:offset + args.batch_size])
    for round_number in range(args.retry_rounds):
        missing = [r for r in reversed(targets) if r["url"] not in archived]
        if args.cached_only or not missing:
            break
        state["round"] = round_number + 1; save(); ready = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {}; queue = iter(missing)
            def submit(_queue=queue, _pending=pending, _executor=executor):
                target = next(_queue, None)
                if target:
                    _pending[_executor.submit(download_one, target, args.cache_directory)] = target
            for _ in range(args.workers):
                submit()
            while pending:
                done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
                if not done:
                    save(); continue
                for future in done:
                    target = pending.pop(future); url = target["url"]
                    state["attempts"][url] = state["attempts"].get(url, 0) + 1
                    try:
                        future.result(); ready.append(target); state["errors"].pop(url, None)
                    except Exception as exc:
                        state["errors"][url] = {"url": url, "stage": "download", "error": str(exc)}
                    submit()
                save()
                if len(ready) >= args.batch_size:
                    checkpoint(ready); ready = []
            checkpoint(ready)
    outstanding = [r["url"] for r in targets if r["url"] not in archived]
    state.update({"status": "partial" if outstanding else "complete", "outstanding_reports": outstanding,
        "archived_reports": len(archived)}); save()
    print(json.dumps({"status": state["status"], "archived": len(archived), "published": len(targets)}), flush=True)


def supervise_retry_delay(failures):
    """Bound repeated failures without repeatedly creating DB tasks."""
    return min(900, 30 * 2 ** min(max(int(failures) - 1, 0), 5))


def record_supervisor_failure(exc, failures):
    path = ROOT / "data/current/pe/history-state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    delay = supervise_retry_delay(failures)
    state.update({"status": "retrying", "last_error": str(exc), "consecutive_failures": failures,
        "retry_after_seconds": delay, "next_retry_at": datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()})
    deadline = time.monotonic() + delay
    while True:
        state["updated_at"] = datetime.now(timezone.utc).isoformat(); atomic_json(path, state)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(30, remaining))
    return state


if __name__ == "__main__":
    failures = 0; previous_progress = None
    while True:
        try:
            main()
            break
        except Exception as exc:
            if "--supervise" not in sys.argv:
                raise
            import traceback
            traceback.print_exc()
            state_path = ROOT / "data/current/pe/history-state.json"
            current = json.loads(state_path.read_text()) if state_path.exists() else {}
            progress = sum(bool(batch.get("applied")) for batch in current.get("batches", {}).values())
            failures = 1 if previous_progress is not None and progress > previous_progress else failures + 1
            previous_progress = progress
            print(json.dumps({"status": "retrying", "error": str(exc),
                "consecutive_failures": failures, "retry_after_seconds": supervise_retry_delay(failures)}), flush=True)
            record_supervisor_failure(exc, failures)
