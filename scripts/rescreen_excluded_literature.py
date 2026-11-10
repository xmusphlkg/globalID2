#!/usr/bin/env python3
"""Rescreen a fixed snapshot of historical exclusions with bounded backpressure."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, or_, select
from src.core.config import get_config
from src.core.database import get_db
from src.domain import LiteratureArticle, LiteratureStatusEvent, Task, TaskStatus, TaskType
from src.literature.screening import initial_screen
from src.literature.clients import PubMedClient
from src.literature.maintenance_cli import ConcurrentApplyError
from src.services.model_operations import enqueue_model_operation
from scripts.backfill_pubmed_abstracts import _apply_pubmed_payload, _exclusive_apply_lock

DEFAULT_DIR = ROOT / "data/cache/literature_historical_rescreen_v1"
ACTOR = "historical-disease-rescreen.v1"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as output:
        os.chmod(temp, 0o600)
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)


def database_identity() -> str:
    return hashlib.sha256(str(get_config().database.url).encode()).hexdigest()


async def initialize(directory: Path) -> tuple[dict, list[int]]:
    state_file, ids_file = directory / "state.json", directory / "ids.json"
    if state_file.exists() or ids_file.exists():
        if not (state_file.exists() and ids_file.exists()):
            raise RuntimeError("incomplete checkpoint: inspect before restarting")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        ids = json.loads(ids_file.read_text(encoding="utf-8"))
        if state.get("database_identity") != database_identity() or len(ids) != state.get("snapshot_count"):
            raise RuntimeError("checkpoint does not match this database or snapshot")
        return state, ids
    async with get_db() as db:
        ids = list((await db.execute(select(LiteratureArticle.id).where(
            LiteratureArticle.publication_status == "excluded"
        ).order_by(LiteratureArticle.id))).scalars().all())
    state = {"version": 1, "run_id": str(uuid.uuid4()), "database_identity": database_identity(),
             "created_at": datetime.now(timezone.utc).isoformat(), "snapshot_count": len(ids),
             "position": 0, "reopened": 0, "needs_evidence": 0, "contextual_review": 0,
             "integrity_blocked": 0, "locked": 0, "changed_before_visit": 0, "status": "running"}
    atomic_json(ids_file, ids)
    atomic_json(state_file, state)
    return state, ids


async def actionable_backlog() -> int:
    ai = LiteratureArticle.metadata_["ai_content_governance"]
    async with get_db() as db:
        count = await db.scalar(select(func.count()).select_from(LiteratureArticle).where(
            LiteratureArticle.publication_status == "review", LiteratureArticle.integrity_status == "current",
            LiteratureArticle.peer_review_status == "peer_reviewed",
            func.length(func.trim(func.coalesce(LiteratureArticle.abstract_text, ""))) >= 80,
            or_(func.coalesce(ai["decision"].as_string(), "") != "hold",
                func.coalesce(ai["second_opinion"].as_boolean(), False).is_(False),
                ai["held_fingerprint"].as_string().is_(None)),
        ))
        return int(count or 0)


async def ensure_article_review() -> str:
    async with get_db() as db:
        tasks = (await db.execute(select(Task).where(
            Task.task_type == TaskType.AGENT_WORKFLOW,
            Task.status.in_((TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)),
            Task.input_data["operation"].as_string() == "governance_stream",
        ))).scalars().all()
        for task in tasks:
            if ((task.input_data or {}).get("arguments") or {}).get("kind") == "articles":
                return str(task.task_uuid)
    task = await enqueue_model_operation("governance_stream", {"kind": "articles"})
    return str(task.task_uuid)


async def candidate_pmids(ids: list[int]) -> list[str]:
    async with get_db() as db:
        values = (await db.execute(select(LiteratureArticle.pmid).where(
            LiteratureArticle.id.in_(ids), LiteratureArticle.publication_status == "excluded",
            LiteratureArticle.integrity_status == "current", LiteratureArticle.pmid.is_not(None),
            LiteratureArticle.pmid != "",
            func.length(func.trim(func.coalesce(LiteratureArticle.abstract_text, ""))) < 80,
        ))).scalars().all()
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


async def process_batch(ids: list[int], run_id: str, abstracts: dict | None = None) -> dict[str, int]:
    counts = {"reopened": 0, "needs_evidence": 0, "contextual_review": 0,
              "integrity_blocked": 0, "locked": 0, "changed_before_visit": 0, "pubmed_updated": 0}
    async with get_db() as db:
        rows = (await db.execute(select(LiteratureArticle).where(
            LiteratureArticle.id.in_(ids)
        ).order_by(LiteratureArticle.id).with_for_update(of=LiteratureArticle))).scalars().all()
        counts["changed_before_visit"] += len(ids) - len(rows)
        for article in rows:
            metadata = dict(article.metadata_ or {})
            prior_run = metadata.get("historical_rescreen") or {}
            if isinstance(prior_run, dict) and prior_run.get("run_id") == run_id:
                # Reconstruct counters if the DB committed but the checkpoint
                # write failed; never reopen a later editorial decision.
                prior_status = prior_run.get("status")
                if prior_status == "integrity_blocked":
                    counts["integrity_blocked"] += 1
                elif prior_status in {"needs_more_evidence", "contextual_review"}:
                    counts["reopened"] += 1
                    counts["needs_evidence" if prior_status == "needs_more_evidence" else "contextual_review"] += 1
                counts["pubmed_updated"] += int(bool(prior_run.get("pubmed_updated")))
                continue
            if article.publication_status != "excluded":
                counts["changed_before_visit"] += 1
                continue
            if metadata.get("editorial_locked"):
                counts["locked"] += 1
                continue
            pubmed_updated = False
            if article.integrity_status == "current" and abstracts and getattr(article, "pmid", None):
                payload = abstracts.get(str(article.pmid).strip())
                if payload and _apply_pubmed_payload(article, payload):
                    counts["pubmed_updated"] += 1
                    pubmed_updated = True
            screen = initial_screen(article)
            metadata["initial_screen"] = {k: v for k, v in screen.items() if k != "controlled_subjects"}
            metadata["historical_rescreen"] = {
                "run_id": run_id, "version": ACTOR, "screened_at": datetime.now(timezone.utc).isoformat(),
                "pubmed_updated": pubmed_updated,
                "previous_autopilot": metadata.get("autopilot"),
                "previous_ai_content_governance": metadata.get("ai_content_governance"),
            }
            if article.integrity_status in {"retracted", "expression_of_concern"}:
                metadata["historical_rescreen"]["status"] = "integrity_blocked"
                article.metadata_ = metadata
                counts["integrity_blocked"] += 1
                continue
            metadata.pop("ai_content_governance", None)
            metadata.pop("screening_hold_reason", None)
            if screen["status"] == "needs_more_evidence":
                metadata["autopilot"] = {"decision": "defer", "policy_version": "research-radar-autopilot.v3",
                                         "actor": ACTOR, "reasons": ["awaiting abstract context"]}
                counts["needs_evidence"] += 1
            else:
                metadata.pop("autopilot", None)
                counts["contextual_review"] += 1
            metadata["historical_rescreen"]["status"] = screen["status"]
            article.metadata_ = metadata
            article.publication_status = "review"
            counts["reopened"] += 1
            db.add(LiteratureStatusEvent(
                article_id=article.article_id, event_type="publication_status_changed", previous_status="excluded",
                current_status="review", source=ACTOR, effective_at=datetime.now(timezone.utc),
                metadata_={"run_id": run_id, "screening_status": screen["status"]},
            ))
        if counts["reopened"] != counts["needs_evidence"] + counts["contextual_review"] or (
            counts["reopened"] + counts["integrity_blocked"] + counts["locked"]
            + counts["changed_before_visit"] != len(ids)
        ):
            raise RuntimeError("historical rescreen batch counts do not reconcile")
    return counts


async def wait_for_new_worker(previous_pid: int) -> None:
    while True:
        process = await asyncio.create_subprocess_exec(
            "systemctl", "show", "globalid-dashboard-worker.service", "-p", "ActiveState", "-p", "MainPID",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await process.communicate()
        values = dict(line.split("=", 1) for line in output.decode().splitlines() if "=" in line)
        try:
            pid = int(values.get("MainPID", "0"))
        except ValueError:
            pid = 0
        if process.returncode == 0 and values.get("ActiveState") == "active" and pid > 0 and pid != previous_pid:
            print(json.dumps({"event": "new_worker_ready", "worker_pid": pid}), flush=True)
            return
        print(json.dumps({"event": "waiting_for_new_worker", "old_pid": previous_pid}), flush=True)
        await asyncio.sleep(30)


async def run(directory: Path, batch_size: int, max_backlog: int, interval: int, max_batches: int | None,
              previous_worker_pid: int | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if previous_worker_pid is not None:
            await wait_for_new_worker(previous_worker_pid)
        state, ids = await initialize(directory)
        cfg = get_config().literature
        pubmed = PubMedClient(contact_email=cfg.contact_email, api_key=getattr(cfg, "pubmed_api_key", ""),
                              tool=getattr(cfg, "pubmed_tool", "GIDSResearchRadar"),
                              timeout_seconds=cfg.request_timeout_seconds, retries=cfg.max_retries,
                              min_interval_seconds=max(1.0, float(getattr(cfg, "pubmed_min_interval_seconds", 0.34) or 0.34)))
        last_enqueue = 0.0
        batches = 0
        while state["position"] < len(ids):
            if max_batches is not None and batches >= max_batches:
                break
            backlog = await actionable_backlog()
            if time.monotonic() - last_enqueue >= 60:
                state["review_task_uuid"] = await ensure_article_review()
                last_enqueue = time.monotonic()
            if backlog >= max_backlog:
                state.update({"status": "waiting_for_review_capacity", "actionable_backlog": backlog})
                atomic_json(directory / "state.json", state)
                print(json.dumps({"event": "waiting", "position": state["position"], "backlog": backlog}), flush=True)
                await asyncio.sleep(interval)
                continue
            start = state["position"]
            effective_size = min(batch_size, 25) if start == 0 else batch_size
            batch = ids[start:start + min(effective_size, max_backlog - backlog)]
            pmids = await candidate_pmids(batch)
            if pmids:
                # Share the existing cross-process lock and never hold a DB
                # transaction open while waiting for a provider response.
                try:
                    with _exclusive_apply_lock():
                        abstracts = await pubmed.fetch_abstracts(pmids, batch_size=100)
                        counts = await process_batch(batch, state["run_id"], abstracts)
                except ConcurrentApplyError:
                    state["status"] = "waiting_for_pubmed_lock"
                    atomic_json(directory / "state.json", state)
                    print(json.dumps({"event": "waiting_for_pubmed_lock", "position": start}), flush=True)
                    await asyncio.sleep(interval)
                    continue
            else:
                counts = await process_batch(batch, state["run_id"])
            for key, value in counts.items():
                state[key] = state.get(key, 0) + value
            state.update({"status": "running", "position": start + len(batch),
                          "actionable_backlog": backlog, "updated_at": datetime.now(timezone.utc).isoformat()})
            atomic_json(directory / "state.json", state)
            print(json.dumps({"event": "batch", "position": state["position"], "total": len(ids), **counts}), flush=True)
            batches += 1
        if state["position"] == len(ids):
            while True:
                backlog = await actionable_backlog()
                state["actionable_backlog"] = backlog
                if backlog == 0:
                    state["status"] = "initial_screen_complete"
                    atomic_json(directory / "state.json", state)
                    break
                state["status"] = "initial_screen_complete_waiting_for_reviews"
                state["review_task_uuid"] = await ensure_article_review()
                atomic_json(directory / "state.json", state)
                print(json.dumps({"event": "waiting_for_reviews", "backlog": backlog}), flush=True)
                await asyncio.sleep(interval)
        print(json.dumps({"event": "stopped", "status": state["status"], "position": state["position"],
                          "total": len(ids)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create the snapshot and process it")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-backlog", type=int, default=2500)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--wait-for-new-worker-pid", type=int,
                        help="Wait until this old worker is replaced by an active new worker")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required; inspect the database before starting")
    if not 1 <= args.batch_size <= 500 or args.max_backlog < args.batch_size or args.interval < 5:
        parser.error("invalid batch size, backlog limit or interval")
    asyncio.run(run(args.directory, args.batch_size, args.max_backlog, args.interval, args.max_batches,
                    args.wait_for_new_worker_pid))


if __name__ == "__main__":
    main()
