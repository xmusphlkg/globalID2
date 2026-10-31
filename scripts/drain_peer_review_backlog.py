#!/usr/bin/env python3
"""Drain the AI peer-review backlog left by the old human/threshold gates.

Order: Research Radar articles first (summaries wait on their parent article),
then summaries, then knowledge sources and learning suggestions.

Articles and summaries run as a fixed worker pool with no batch boundaries: a
feeder claims rows, ``--workers`` tasks each review one row and immediately take
the next, and a writer commits finished reviews every few dozen rows or seconds.
A slow row only occupies its own worker. The run is safe to stop and restart:
unfinished rows stay in review. When model calls keep failing the feeder pauses,
then gives up; the next run retries those rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.model_operations import run_worker_operation  # noqa: E402


def log(stage: str, payload: dict) -> None:
    keep = {k: v for k, v in payload.items() if k != "applied"}
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "stage": stage, **keep}, ensure_ascii=False), flush=True)


async def drain_stream(kind: str, workers: int, commit_every: int) -> None:
    totals = await run_worker_operation("governance_stream", {
        "kind": kind, "workers": workers, "commit_every": commit_every})
    log(f"{kind}_done", {"totals": totals})


async def drain_simple(name: str, method, batch: int) -> None:
    while True:
        result = await run_worker_operation("governance_simple", {"method": method, "limit": batch})
        log(name, result)
        if result["reviewed"] == 0 or result["deferred"] == result["reviewed"]:
            return


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=96, help="model-calling workers in flight")
    parser.add_argument("--commit-every", type=int, default=25, help="finished rows per database commit")
    parser.add_argument("--batch", type=int, default=64, help="rows per pass for sources and learning")
    parser.add_argument("--only", choices=["articles", "summaries", "sources", "learning"])
    args = parser.parse_args()
    stages = {
        "articles": lambda: drain_stream("articles", args.workers, args.commit_every),
        "summaries": lambda: drain_stream("summaries", args.workers, args.commit_every),
        "sources": lambda: drain_simple("knowledge_sources", "review_knowledge_sources", args.batch),
        "learning": lambda: drain_simple("learning_suggestions", "review_learning_suggestions", args.batch),
    }
    for name, run in stages.items():
        if args.only and name != args.only:
            continue
        await run()
    log("done", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
