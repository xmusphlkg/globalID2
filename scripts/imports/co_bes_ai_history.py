#!/usr/bin/env python3
"""Run a frozen BES page plan through the existing durable model center."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from scripts.imports.import_surveillance_archive import maintain_lease
from src.ai.runtime import task_context, wait_for_activities
from src.ai.runtime.contracts import TERMINAL_STATUSES
from src.ai.runtime.store import request_cancellation
from src.core.database import dispose_database, get_db
from src.data.crawlers.co_bes import BESReport
from src.domain import Country, Task, TaskStatus, TaskType
from src.domain.ai_work_item import AIWorkItem
from src.services.ingestion.co_bes_ai import extract_report


def save(path: Path, document: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load_plan(path: Path, limit=None):
    payload = path.read_bytes()
    document = json.loads(payload)
    entries = document["reports"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Nonempty BES page plan required")
    if limit is not None:
        entries = entries[:limit]
    selected = []
    for entry in entries:
        pdf = (ROOT / entry["pdf_path"]).resolve()
        if not pdf.is_relative_to((ROOT / "data/raw/co").resolve()) or not pdf.is_file():
            raise ValueError("BES PDF must exist inside data/raw/co")
        pages = entry["pages"]
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError("BES plan requires positive one-based PDF pages")
        report = BESReport(**entry["report"])
        selected.append((entry, pdf, report, sorted(set(pages))))
    return selected, hashlib.sha256(payload).hexdigest()


async def drain_children(task_uuid, *, cancel=False):
    """Keep the parent RUNNING until all durable model children are terminal."""
    if cancel:
        async with get_db() as db:
            parent = await db.scalar(select(Task).where(Task.task_uuid == task_uuid).with_for_update())
            parent.metadata_ = {**(parent.metadata_ or {}), "cancel_requested": True}
    while True:
        async with get_db() as db:
            children = list((await db.scalars(select(AIWorkItem).where(
                AIWorkItem.parent_task_uuid == task_uuid))).all())
            active = [child for child in children if child.status not in TERMINAL_STATUSES]
            if cancel:
                for child in active:
                    await request_cancellation(db, child)
            ids = [child.id for child in active]
            statuses = dict(Counter(child.status for child in children))
        if not ids:
            return statuses
        await wait_for_activities(ids, parent_task_uuid=task_uuid, timeout=2)
        if cancel:
            await asyncio.sleep(0.25)


async def finish_parent(task_uuid, *, status, state):
    async with get_db() as db:
        parent = await db.scalar(select(Task).where(Task.task_uuid == task_uuid).with_for_update())
        parent.status = status
        parent.completed_at = datetime.now(timezone.utc)
        parent.completed_steps = len(state["reports"])
        parent.progress = 100 if status == TaskStatus.COMPLETED else int(100 * len(state["reports"]) / state["total_reports"])
        parent.output_data = state
        parent.last_error = None if status == TaskStatus.COMPLETED else state.get("error_type", "One or more BES AI reports failed")


async def run(args):
    args.output = args.output.resolve()
    selected, plan_digest = load_plan(args.plan, args.limit)
    task_uuid = str(uuid4())
    started = datetime.now(timezone.utc)
    owner = f"co-bes-ai:{task_uuid}"
    state = {"task_uuid": task_uuid, "status": "running", "source": "co_ins_bes_weekly",
             "plan": str(args.plan.resolve()), "plan_sha256": plan_digest,
             "started_at": started.isoformat(), "total_reports": len(selected),
             "concurrency": args.concurrency, "reports": [], "database_imported": False,
             "public_release_enabled": False}
    task = Task(task_uuid=task_uuid, task_type=TaskType.PROCESS_DATA,
                task_name=f"CO BES model-center extraction: {len(selected)} reports",
                status=TaskStatus.RUNNING, started_at=started, total_steps=len(selected),
                input_data={"operation": "co_bes_ai_history", "country": "CO",
                            "source": "co_ins_bes_weekly", "plan_sha256": plan_digest},
                metadata_={"origin": "operator_authorized_backfill", "public_release_enabled": False,
                           "task_lease": {"owner": owner, "heartbeat_at": started.isoformat()}})
    async with get_db() as db:
        task.country_id = await db.scalar(select(Country.id).where(Country.code == "CO"))
        db.add(task)
    save(args.output, state)
    print(json.dumps({"task_uuid": task_uuid, "status": "running", "reports": len(selected)}), flush=True)
    heartbeat = asyncio.create_task(maintain_lease(task_uuid, owner))
    semaphore = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    async def one(index, item):
        entry, pdf, report, pages = item
        async with semaphore:
            result = {"index": index, "pdf_path": str(pdf.relative_to(ROOT)),
                      "pdf_sha256": entry["pdf_sha256"], "pages": pages,
                      "started_at": datetime.now(timezone.utc).isoformat()}
            try:
                options = {"cache_directory": args.cache_directory}
                if entry.get("partial_report"):
                    options["partial_report"] = True
                extracted = await extract_report(pdf, report, pages, **options)
                result_path = args.output.parent / "bes-ai-results" / task_uuid / f"{index:03d}-{entry['pdf_sha256']}.json"
                save(result_path, extracted)
                result.update(status="completed", result_path=str(result_path.relative_to(ROOT)),
                              validated=bool(extracted.get("validated")),
                              cells=len(extracted.get("cells", [])))
                if not result["validated"]:
                    result.update(status="failed", error_type="UnvalidatedExtraction")
            except Exception as exc:  # noqa: BLE001 - persist individual report failure; cancellation propagates.
                result.update(status="failed", error_type=type(exc).__name__)
                from src.services._lifecycle import safe_exception_summary
                result["error"] = safe_exception_summary(exc)
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            async with lock:
                state["reports"].append(result)
                state["reports"].sort(key=lambda r: r["index"])
                save(args.output, state)
                async with get_db() as db:
                    parent = await db.scalar(select(Task).where(Task.task_uuid == task_uuid).with_for_update())
                    parent.completed_steps = len(state["reports"])
                    parent.progress = int(100 * len(state["reports"]) / len(selected))
                    parent.output_data = dict(state)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

    jobs = []
    try:
        async with task_context(task):
            jobs = [asyncio.create_task(one(index, item)) for index, item in enumerate(selected, 1)]
            await asyncio.gather(*jobs)
            state["model_work_statuses"] = await drain_children(task_uuid)
        failed = sum(report["status"] != "completed" for report in state["reports"])
        state.update(status="failed" if failed else "completed", failed_reports=failed,
                     finished_at=datetime.now(timezone.utc).isoformat())
        await finish_parent(task_uuid, status=TaskStatus.FAILED if failed else TaskStatus.COMPLETED, state=state)
        save(args.output, state)
        return 2 if failed else 0
    except BaseException as exc:
        for job in jobs:
            job.cancel()
        async def cleanup(error=exc):
            await asyncio.gather(*jobs, return_exceptions=True)
            state["model_work_statuses"] = await drain_children(task_uuid, cancel=True)
            cancelled = isinstance(error, (asyncio.CancelledError, KeyboardInterrupt))
            state.update(status="cancelled" if cancelled else "failed", error_type=type(error).__name__,
                         finished_at=datetime.now(timezone.utc).isoformat())
            await finish_parent(task_uuid, status=TaskStatus.CANCELLED if cancelled else TaskStatus.FAILED, state=state)
            save(args.output, state)
        cleanup_task = asyncio.create_task(cleanup())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        cleanup_task.result()
        raise
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--plan", type=Path, default=ROOT / "data/current/co/bes-ai-page-plan.json")
    result.add_argument("--concurrency", type=int, choices=range(1, 33), default=12)
    result.add_argument("--limit", type=int)
    result.add_argument("--cache-directory", type=Path)
    result.add_argument("--output", type=Path, default=ROOT / "data/current/co/bes-ai-history-state.json")
    return result


async def main():
    args = parser().parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    try:
        return await run(args)
    finally:
        await dispose_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
