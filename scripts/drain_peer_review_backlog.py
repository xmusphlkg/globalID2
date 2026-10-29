#!/usr/bin/env python3
"""Drain the AI peer-review backlog left by the old human/threshold gates.

Order: Research Radar articles first (summaries wait on their parent article),
then summaries by id cursor, then knowledge sources and learning suggestions.
Each batch commits on its own, so the run is safe to stop and restart. A batch
that reports a model outage pauses and retries instead of spinning.
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

from src.core import get_db  # noqa: E402
from src.services.ai_content_governance_service import ai_content_governance_service as gov  # noqa: E402

OUTAGE_PAUSE_SECONDS = 300


def log(stage: str, payload: dict) -> None:
    keep = {k: v for k, v in payload.items() if k != "applied"}
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "stage": stage, **keep}, ensure_ascii=False), flush=True)


#: Consecutive all-deferred batches before a stage gives up; the next run retries them.
MAX_STALLED_BATCHES = 3


async def drain_articles(batch: int) -> None:
    stalled = 0
    while True:
        async with get_db() as db:
            result = await gov.review_literature_articles(db, limit=batch)
        log("articles", result)
        if result["reviewed"] == 0:
            return
        if result["deferred"] == result["reviewed"]:
            # Only items whose model replies keep failing remain at the head of
            # the queue (or every route is down): pause, then stop retrying.
            stalled += 1
            if stalled >= MAX_STALLED_BATCHES:
                log("articles_stalled", {"remaining_deferred": result["deferred"]})
                return
            await asyncio.sleep(OUTAGE_PAUSE_SECONDS)
        else:
            stalled = 0


async def drain_summaries(batch: int) -> None:
    cursor = 0
    stalled = 0
    while True:
        async with get_db() as db:
            result = await gov.review_literature_summaries(db, limit=batch, after_id=cursor)
        log("summaries", {**result, "cursor": cursor})
        if result["reviewed"] == 0:
            return
        if result["route_error"] and result["deferred"] == result["reviewed"] and stalled < MAX_STALLED_BATCHES:
            # Whole window failed: likely an outage, retry the same window.
            stalled += 1
            await asyncio.sleep(OUTAGE_PAUSE_SECONDS)
            continue
        # Partial failures move on; deferred items stay in review for the next run.
        stalled = 0
        cursor = max(int(item["summary_id"]) for item in result["applied"]) if result["applied"] else cursor + batch


async def drain_simple(name: str, method, batch: int) -> None:
    while True:
        async with get_db() as db:
            result = await method(db, limit=batch)
        log(name, result)
        if result["reviewed"] == 0 or result["deferred"] == result["reviewed"]:
            return


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--only", choices=["articles", "summaries", "sources", "learning"])
    args = parser.parse_args()
    stages = {
        "articles": lambda: drain_articles(args.batch),
        "summaries": lambda: drain_summaries(args.batch),
        "sources": lambda: drain_simple("knowledge_sources", gov.review_knowledge_sources, args.batch),
        "learning": lambda: drain_simple("learning_suggestions", gov.review_learning_suggestions, args.batch),
    }
    for name, run in stages.items():
        if args.only and name != args.only:
            continue
        await run()
    log("done", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
