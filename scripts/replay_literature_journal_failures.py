#!/usr/bin/env python3
"""Inspect failed journal windows or enqueue one bounded, isolated replay page."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with redirect_stderr(io.StringIO()):
    from loguru import logger
    from sqlalchemy import select

    from src.core import dispose_database, get_database
    from src.domain import LiteratureIngestRun, TaskPriority
    from src.literature.replay import REPLAY_SOURCE, failed_journal_windows
    from src.services.model_operations import enqueue_model_operation

logger.remove()


async def run(*, days: int, apply: bool, replay_key: str | None) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_database() as db:
        runs = list((await db.execute(
            select(LiteratureIngestRun)
            .where(
                LiteratureIngestRun.started_at >= cutoff,
                LiteratureIngestRun.status == "completed",
                LiteratureIngestRun.source.like("crossref%"),
            )
            .order_by(LiteratureIngestRun.started_at, LiteratureIngestRun.id)
            .limit(10001)
        )).scalars())
        if len(runs) > 10000:
            raise ValueError("history_limit_exceeded_reduce_days")
        windows = failed_journal_windows(runs)
        latest = {}
        for window in windows:
            row = (await db.execute(
                select(LiteratureIngestRun)
                .where(
                    LiteratureIngestRun.source == REPLAY_SOURCE,
                    LiteratureIngestRun.status == "completed",
                    LiteratureIngestRun.checkpoint["replay_key"].as_string() == window.key,
                )
                .order_by(LiteratureIngestRun.completed_at.desc(), LiteratureIngestRun.id.desc())
                .limit(1)
            )).scalar_one_or_none()
            latest[window.key] = row.checkpoint if row is not None else {}
        await db.rollback()
    plan = []
    for window in windows:
        cp = latest[window.key]
        from src.literature.pipeline import _parse_checkpoint_datetime
        through = _parse_checkpoint_datetime(cp.get("through_indexed_at"))
        complete = bool(through and through >= window.until and not cp.get("truncated") and not cp.get("source_errors"))
        plan.append({
            **window.as_dict(),
            "replay_key": window.key,
            "complete": complete,
            "next_from_indexed_at": cp.get("next_from_indexed_at"),
        })
    result = {"mode": "apply" if apply else "dry_run", "window_count": len(plan), "windows": plan}
    if apply:
        selected = next((w for w in windows if w.key == replay_key), None)
        if selected is None:
            raise ValueError("replay_key_not_in_current_plan")
        entry = next(p for p in plan if p["replay_key"] == replay_key)
        if entry["complete"]:
            result["status"] = "already_completed"
        else:
            task = await enqueue_model_operation(
                "literature_replay", selected.as_dict(), priority=TaskPriority.LOW,
            )
            result.update(status="queued", task_uuid=task.task_uuid)
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="Queue one page after the updated worker is running")
    parser.add_argument("--replay-key", help="Exact window key from the read-only plan; required for --apply")
    args = parser.parse_args()
    if not 1 <= args.days <= 365 or (args.apply and not args.replay_key):
        parser.error("--days must be 1..365; --apply requires --replay-key")
    try:
        result = await run(days=args.days, apply=args.apply, replay_key=args.replay_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    finally:
        await dispose_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
