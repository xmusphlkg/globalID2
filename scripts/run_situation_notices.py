#!/usr/bin/env python3
"""Queue collection and extraction; --export-only publishes stored Situation data."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.global_sources.collection import DEFAULT_SINCE_DAYS  # noqa: E402
from src.services.situation_notices.export import DEFAULT_OUTPUT_DIR  # noqa: E402
from src.services.situation_notices.runner import export_situation  # noqa: E402


async def _enqueue_refresh(args) -> dict:
    from src.core.task_manager import task_manager
    from src.domain import TaskType, TaskStatus, TaskPriority
    task = await task_manager.create_task(
        task_type=TaskType.REFRESH_SITUATION_SOURCES,
        task_name="Refresh Situation Sources",
        priority=TaskPriority.NORMAL,
        input_data={"since_days": args.since_days, "don_limit": args.don_limit,
                    "document_limit": args.document_limit, "ai_limit": args.ai_limit,
                    "skip_ai": args.skip_ai, "fetch": not args.no_fetch,
                    "require_cross_model": args.require_cross_model},
    )
    await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED)
    return {"task_uuid": task.task_uuid, "status": "queued"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the alert-based Situation pipeline")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--don-limit", type=int, default=200)
    parser.add_argument("--document-limit", type=int, default=40, help="Report bodies downloaded per run")
    parser.add_argument("--ai-limit", type=int, default=30, help="Reports read by the models per run")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--skip-ai", action="store_true", help="Collect and store without model calls")
    parser.add_argument("--no-fetch", action="store_true", help="Reuse stored reports; no feed requests")
    parser.add_argument(
        "--require-cross-model",
        action="store_true",
        help="Refuse to run unless two distinct model routes are available",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Release step: export from the database; no feed requests, no model calls",
    )
    args = parser.parse_args()

    if args.export_only:
        summary = asyncio.run(export_situation(output_dir=args.output_dir))
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if summary["export"]["alert_count"] == 0 and summary["export"]["monitoring_products"] == 0:
            raise SystemExit(2)
        return

    # Model work is executed only by the worker dispatcher. The CLI uses the
    # same durable task entrypoint as the scheduler, including --skip-ai.
    summary = asyncio.run(_enqueue_refresh(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))



if __name__ == "__main__":
    main()
