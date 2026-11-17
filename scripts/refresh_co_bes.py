#!/usr/bin/env python3
"""Refresh private INS BES source facts from the two latest epidemiological years.

Run once from a dedicated systemd timer. The accepted public release and its
general workers are outside this runner's ownership.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.imports.co_bes_history import save_state
from scripts.imports.import_surveillance_archive import import_archive
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from scripts.verify_co_bes_history import verify
from src.data.crawlers.co_bes import DEFAULT_SCOPE, BESContractError
from src.data.crawlers.co_bes import PARSER_VERSION as ANNEX_VERSION
from src.data.crawlers.co_bes_weekly import PARSER_VERSION as WEEKLY_VERSION
from src.services.ingestion.countries.co_bes import collect

STATE_PATH = ROOT / "data/current/co/bes-refresh-state.json"
BASELINE_RECEIPT = ROOT / "data/current/co/bes_database_import_receipt.json"
BASELINE_ACCEPTANCE = ROOT / "data/current/co/bes_database_acceptance.json"
AUTOMATION_JOB_ID = "co-co-ins-bes-weekly-daily"
STALE_AFTER = timedelta(minutes=10)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _applied_from_baseline(receipt_path: Path, acceptance_path: Path) -> dict[str, str]:
    receipt = json.loads(receipt_path.read_bytes())
    acceptance = json.loads(acceptance_path.read_bytes())
    audits = receipt.get("source_audit", {}).get("report_audits", [])
    if (receipt.get("source") != DEFAULT_SCOPE
            or receipt.get("mode") != "database_imported"
            or not acceptance.get("facts_match")
            or acceptance.get("source") != DEFAULT_SCOPE
            or acceptance.get("source_rows") != receipt.get("records")
            or Path(acceptance.get("source_manifest", "")).resolve()
            != Path(receipt.get("source_manifest", "")).resolve()
            or acceptance.get("captured_pdf_count") != len(audits)
            or acceptance.get("represented_pdf_count") != len(audits)
            or acceptance.get("missing_database_rows") != 0
            or acceptance.get("mismatched_rows") != 0
            or receipt.get("source_audit", {}).get("failed_reports")):
        raise BESContractError("Verified BES database baseline is required before automatic refresh")
    applied = {item["source_url"]: item["sha256"] for item in audits
               if item.get("parsed_rows", 0) > 0 and item.get("source_url") and item.get("sha256")}
    if len(applied) != len(audits) or not applied:
        raise BESContractError("BES baseline report identities are incomplete")
    manifest_path = Path(receipt["source_manifest"]).resolve()
    if (not manifest_path.is_relative_to((ROOT / "data/raw/co").resolve())
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            != acceptance.get("source_manifest_sha256")):
        raise BESContractError("BES accepted manifest identity changed")
    manifest = json.loads(manifest_path.read_bytes())
    originals = {item["metadata_"]["report"]["pdf_url"]: item["sha256"]
                 for item in manifest["artifacts"]
                 if item.get("metadata_", {}).get("endpoint") == "pdf"}
    if originals != applied:
        raise BESContractError("BES baseline report hashes differ from accepted originals")
    actionable = [gap for gap in receipt["source_audit"].get("parser_gaps", [])
                  if not gap.get("source_cell_retained")]
    if actionable:
        raise BESContractError("BES baseline has unresolved actionable report gaps")
    return applied


def load_state(path: Path, receipt_path: Path, acceptance_path: Path) -> dict:
    if path.exists():
        state = json.loads(path.read_bytes())
        applied = state.get("applied_reports")
        if state.get("schema") != 1 or not isinstance(applied, dict) or not applied:
            raise BESContractError("BES refresh state is invalid")
    else:
        state = {"schema": 1, "source": DEFAULT_SCOPE,
                 "applied_reports": _applied_from_baseline(receipt_path, acceptance_path),
                 "baseline_receipt": str(receipt_path), "baseline_acceptance": str(acceptance_path)}
        save_state(path, state)
    if state.get("source") != DEFAULT_SCOPE or any(
        not isinstance(url, str) or not url.startswith("https://www.ins.gov.co/")
        or not isinstance(digest, str) or len(digest) != 64
        for url, digest in state["applied_reports"].items()
    ):
        raise BESContractError("BES refresh state source identities are invalid")
    return state


class DeltaSnapshotWriter(FileSnapshotWriter):
    """Retain only changed PDFs while preserving the full discovery receipt."""

    def __init__(self, directory: Path, applied: dict[str, str]):
        super().__init__(directory)
        self.applied = applied
        self.pending: dict[str, str] = {}
        self.failed_downloads: list[dict] = []
        self.discovered_reports = 0
        self.listed_urls: set[str] = set()

    async def write(self, payload, **options):
        metadata = options.get("metadata") or {}
        endpoint = metadata.get("endpoint")
        if endpoint == "pdf":
            url = metadata["report"]["pdf_url"]
            digest = hashlib.sha256(payload).hexdigest()
            if self.applied.get(url) == digest:
                return SimpleNamespace(sha256=digest)
            await super().write(payload, **options)
            self.pending[url] = digest
            return self.artifacts[-1]
        if endpoint == "collection_plan":
            full = json.loads(payload)
            self.discovered_reports = len(full["reports"])
            self.listed_urls = {item["pdf_url"] for item in full["reports"]}
            self.failed_downloads = full["failed_downloads"]
            await super().write(payload, **(options | {"metadata": metadata | {
                "endpoint": "full_collection_plan"}}))
            selected = set(self.pending)
            delta = full | {
                "reports": [item for item in full["reports"] if item["pdf_url"] in selected],
                "captured": [item for item in full["captured"] if item["url"] in selected],
                "failed_downloads": [],
                "deferred_failed_downloads": full["failed_downloads"],
            }
            payload = json.dumps(delta, sort_keys=True).encode()
        await super().write(payload, **options)
        return self.artifacts[-1]


def _stale_owned_task(task, now: datetime) -> bool:
    from src.domain import TaskStatus

    metadata = task.metadata_ or {}
    lease = metadata.get("task_lease") or {}
    if (task.status != TaskStatus.RUNNING
            or metadata.get("origin") != "co_bes_automatic_refresh"
            or metadata.get("automation_job_id") != AUTOMATION_JOB_ID
            or not lease.get("owner") or lease.get("released_at")
            or not task.started_at or not lease.get("heartbeat_at")):
        return False
    try:
        heartbeat = datetime.fromisoformat(lease["heartbeat_at"])
    except (TypeError, ValueError):
        return False
    started = task.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    return now - started > STALE_AFTER and now - heartbeat > STALE_AFTER


async def _ensure_no_other_bes_task() -> None:
    from sqlalchemy import select

    from src.ai.runtime.store import request_cancellation
    from src.core.database import get_db
    from src.domain import Country, Task, TaskStatus, TaskType
    from src.domain.ai_work_item import AIWorkItem

    now = datetime.now(timezone.utc)
    active = []
    async with get_db() as db:
        tasks = (await db.execute(
            select(Task).join(Country, Task.country_id == Country.id).where(
                Country.code == "CO", Task.task_type == TaskType.CRAWL_DATA,
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING]),
                Task.input_data["source"].as_string() == DEFAULT_SCOPE,
            ).with_for_update(of=Task)
        )).scalars().all()
        for task in tasks:
            if not _stale_owned_task(task, now):
                active.append(task.task_uuid)
                continue
            children = (await db.scalars(select(AIWorkItem).where(
                AIWorkItem.parent_task_uuid == task.task_uuid,
                AIWorkItem.status.in_(["queued", "leased"]),
            ))).all()
            for item in children:
                await request_cancellation(db, item)
            metadata = dict(task.metadata_ or {})
            lease = dict(metadata["task_lease"])
            lease.update(released_at=now.isoformat(), terminal_status=TaskStatus.FAILED.value)
            metadata["task_lease"] = lease
            task.metadata_ = metadata
            task.status = TaskStatus.FAILED
            task.completed_at = now
            task.last_error = "BES automatic refresh owner heartbeat expired"
            task.retry_count = int(task.retry_count or 0) + 1
    if active:
        raise BESContractError("Another BES crawl task is queued or active")


async def refresh_once(args, state: dict) -> dict:
    await _ensure_no_other_bes_task()
    versions = {"annex": ANNEX_VERSION, "weekly_notifications": WEEKLY_VERSION}
    if state.get("contract_versions") != versions:
        # Source bytes alone cannot prove the new parser has been applied.
        # Replay once on a contract change, then resume hash-based deltas.
        state["applied_reports"] = {}
        state["contract_versions"] = versions
    old_pending = set(state.get("pending_reports", []))
    old_failed = {item["url"]: item for item in state.get("failed_downloads", [])}
    state.pop("error_type", None)
    current_year = datetime.now(ZoneInfo("America/Bogota")).year
    start_year = max(2025, current_year - 1)
    retry_urls = [*state.get("pending_reports", []),
                  *(item["url"] for item in state.get("failed_downloads", []))]
    retry_years = [int(match.group()) for url in retry_urls
                   if (match := re.search(r"(?<!\d)20\d{2}(?!\d)", url))]
    if retry_years:
        start_year = max(2025, min(start_year, *retry_years))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    writer = DeltaSnapshotWriter(
        ROOT / "data/raw/co/bes-refresh" / stamp, state["applied_reports"])
    state.update(status="capturing", started_at=_utc_now(), start_year=start_year,
                 end_year=current_year, raw_manifest=str(writer.directory / "manifest.json"))
    save_state(args.state_path, state)
    await collect(writer, source=DEFAULT_SCOPE, start_year=start_year, end_year=current_year,
                  download_workers=args.download_workers, parse_workers=args.parse_workers,
                  cache_directory=args.cache_directory)
    state.update(discovered_reports=writer.discovered_reports,
                 changed_reports=len(writer.pending),
                 failed_downloads=[*writer.failed_downloads,
                                   *(item for url, item in old_failed.items()
                                     if url not in writer.listed_urls)],
                 unlisted_pending=sorted((old_pending | set(old_failed)) - writer.listed_urls),
                 pending_reports=sorted(old_pending | set(writer.pending)))
    save_state(args.state_path, state)
    if writer.pending:
        await _ensure_no_other_bes_task()
        receipt_path = ROOT / "data/current/co/bes-refresh-receipts" / f"{stamp}.json"
        receipt = await import_archive(
            "CO", writer.directory / "manifest.json", apply=True, source=DEFAULT_SCOPE,
            start_year=start_year, parse_workers=args.parse_workers,
            receipt_path=receipt_path, task_metadata={
                "origin": "co_bes_automatic_refresh", "scheduled_trigger": True,
                "automation_job_id": AUTOMATION_JOB_ID, "runtime_backend": "systemd",
            })
        state["last_receipt"] = str(receipt_path)
        reconciliation_path = receipt_path.with_name(f"{stamp}-reconciliation.json")
        reconciliation = await verify(
            writer.directory / "manifest.json", start_year=start_year,
            end_year=current_year, parse_workers=args.parse_workers,
            only_manifest_reports=True)
        save_state(reconciliation_path, reconciliation)
        state["last_reconciliation"] = str(reconciliation_path)
        state["reconciliation_facts_match"] = reconciliation["facts_match"]
        if not reconciliation["facts_match"]:
            state.update(status="retry_pending", finished_at=_utc_now())
            save_state(args.state_path, state)
            return state
        audit = receipt["source_audit"]
        actionable = {gap["report_url"] for gap in audit.get("parser_gaps", [])
                      if not gap.get("source_cell_retained")}
        successful = {item["source_url"]: item["sha256"] for item in audit.get("report_audits", [])
                      if item.get("parsed_rows", 0) > 0 and item.get("source_url") not in actionable}
        if any(writer.pending.get(url) != digest for url, digest in successful.items()):
            raise BESContractError("BES import report identity differs from captured delta")
        state["applied_reports"].update(successful)
        old_pending.difference_update(successful)
        state["last_task_uuid"] = receipt["task_uuid"]
        state["last_imported_records"] = receipt["records"]
        state["failed_reports"] = audit.get("failed_reports", [])
    else:
        state["failed_reports"] = []
    state["pending_reports"] = sorted(old_pending | {
        url for url, digest in writer.pending.items()
        if state["applied_reports"].get(url) != digest})
    state["status"] = ("retry_pending" if state["failed_downloads"] or state["pending_reports"]
                       or state["unlisted_pending"]
                       else "up_to_date")
    state["finished_at"] = _utc_now()
    save_state(args.state_path, state)
    return state


async def run(args) -> tuple[dict, int]:
    from src.core.database import dispose_database

    args.state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.state_path.with_suffix(".lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"source": DEFAULT_SCOPE, "status": "already_running"}, 3
            state = load_state(args.state_path, args.baseline_receipt, args.baseline_acceptance)
            for attempt in range(1, args.attempts + 1):
                state["attempt"] = attempt
                try:
                    await refresh_once(args, state)
                    if state["status"] == "up_to_date":
                        return state, 0
                except Exception as exc:  # noqa: BLE001 - persisted CLI failure boundary
                    state.update(status="retry_pending", error_type=type(exc).__name__,
                                 finished_at=_utc_now())
                    save_state(args.state_path, state)
                if attempt < args.attempts:
                    await asyncio.sleep(args.retry_delay * attempt)
            return state, 2
    finally:
        await dispose_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--baseline-receipt", type=Path, default=BASELINE_RECEIPT)
    parser.add_argument("--baseline-acceptance", type=Path, default=BASELINE_ACCEPTANCE)
    parser.add_argument("--cache-directory", type=Path,
                        default=ROOT / "data/cache/co/ins_bes_reports")
    parser.add_argument("--download-workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--parse-workers", type=int, choices=range(1, 33), default=4)
    parser.add_argument("--apply", action="store_true", help="Required to persist source facts")
    parser.add_argument("--attempts", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--retry-delay", type=int, default=30)
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required; this runner writes source facts")
    if args.retry_delay < 0:
        parser.error("--retry-delay must be nonnegative")
    state, code = asyncio.run(run(args))
    summary = {key: state.get(key) for key in (
        "source", "status", "started_at", "finished_at", "start_year", "end_year",
        "discovered_reports", "changed_reports", "pending_reports", "failed_downloads",
        "unlisted_pending",
        "last_task_uuid", "last_imported_records", "error_type")}
    summary["applied_report_count"] = len(state.get("applied_reports", {}))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
