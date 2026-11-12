#!/usr/bin/env python3
"""Continuously queue bounded historical abstract repairs and fresh review passes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config
from src.core.database import dispose_database
from src.domain import TaskPriority
from src.literature.maintenance_cli import exclusive_apply_lock
from src.services.model_operations import (
    enqueue_model_operation,
    wait_task_output,
)

STATE = ROOT / "data/cache/literature_abstract_backfill_state.json"
LOCK = ROOT / "data/cache/literature_abstract_backfill_controller.lock"


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, STATE)


def read_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


async def fresh_review(after: datetime) -> None:
    """Wait for earlier streams, then require a stream created after repairs."""
    while True:
        task = await enqueue_model_operation(
            "governance_stream",
            {"kind": "articles", "buffer_size": 512},
            priority=TaskPriority.LOW,
        )
        created = task.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        print(
            json.dumps(
                {
                    "event": "review_wait",
                    "task_uuid": task.task_uuid,
                    "fresh": created >= after,
                }
            ),
            flush=True,
        )
        await wait_task_output(task.task_uuid)
        if created >= after:
            return


async def run(*, batches_per_review: int, empty_wait_seconds: int) -> None:
    cfg = get_config().literature
    state = read_state()
    review_job = None

    async def review_pending():
        while state.get("pending_review"):
            revision = state.get("revision", 0)
            await fresh_review(datetime.now(timezone.utc))
            if revision == state.get("revision", 0):
                state["pending_review"] = False
                save_state(state)

    def ensure_review():
        nonlocal review_job
        if review_job is None or review_job.done():
            if review_job is not None:
                review_job.result()
            review_job = asyncio.create_task(review_pending())

    if state.get("pending_review"):
        ensure_review()
    while True:
        if not (cfg.abstract_repair_enabled and cfg.abstract_repair_controller_enabled):
            print(json.dumps({"event": "disabled", "seconds": empty_wait_seconds}), flush=True)
            await asyncio.sleep(empty_wait_seconds)
            continue
        examined = filled = 0
        for _ in range(batches_per_review):
            state["pending_review"] = True
            state["revision"] = state.get("revision", 0) + 1
            save_state(state)
            task = await enqueue_model_operation(
                "literature_abstract_repair",
                {
                    "limit": cfg.abstract_repair_batch_size,
                    "retry_hours": cfg.abstract_repair_retry_after_hours,
                },
                priority=TaskPriority.LOW,
            )
            print(
                json.dumps({"event": "repair_wait", "task_uuid": task.task_uuid}),
                flush=True,
            )
            output = await wait_task_output(task.task_uuid)
            result = output.get("result") or {}
            examined += int(result.get("examined") or 0)
            filled += int(result.get("filled") or 0)
            print(
                json.dumps(
                    {
                        "event": "repair_finished",
                        "task_uuid": task.task_uuid,
                        "counts": result,
                    }
                ),
                flush=True,
            )
            if int(result.get("examined") or 0) < cfg.abstract_repair_batch_size:
                break
        # Restart recovery handles an uncertain batch. A completed cycle with no
        # changes does not need an expensive full review scan.
        if filled or state.get("pending_review"):
            ensure_review()
        if examined < cfg.abstract_repair_batch_size:
            print(
                json.dumps(
                    {
                        "event": "rest",
                        "seconds": empty_wait_seconds,
                        "examined": examined,
                        "filled": filled,
                    }
                ),
                flush=True,
            )
            await asyncio.sleep(empty_wait_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches-per-review", type=int, default=5)
    parser.add_argument("--empty-wait-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not 1 <= args.batches_per_review <= 100 or args.empty_wait_seconds < 60:
        parser.error(
            "batches-per-review must be 1–100 and empty-wait-seconds at least 60"
        )

    async def entry():
        try:
            await run(
                batches_per_review=args.batches_per_review,
                empty_wait_seconds=args.empty_wait_seconds,
            )
        finally:
            await dispose_database()

    with exclusive_apply_lock(LOCK, operation="abstract backfill controller"):
        asyncio.run(entry())


if __name__ == "__main__":
    main()
