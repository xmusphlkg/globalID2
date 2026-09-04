#!/usr/bin/env python3
"""Inspect or explicitly recover task rows left RUNNING by a dead worker."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.control_plane.runtime import runtime_registry
from src.core import dispose_database, get_database, get_config
from src.domain import Task, TaskStatus
from src.services.task_executor import (
    RECOVERABLE_IDEMPOTENT_TASK_TYPES,
    _task_heartbeat_at,
    recover_interrupted_tasks_on_startup,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run by default; --apply persists bounded stale-task recovery."
    )
    parser.add_argument("--apply", action="store_true", help="persist recovery actions")
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow --apply while a worker heartbeat is live (normally unsafe)",
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=get_config().task_worker.stale_task_seconds,
    )
    parser.add_argument(
        "--owner",
        help="restrict inspection and recovery to one exact persisted task-lease owner",
    )
    parser.add_argument(
        "--confirmed-dead-owner",
        action="store_true",
        help=(
            "allow an immediate owner-scoped recovery after maintenance tooling "
            "verified the process is dead and compare-deleted its Redis lease"
        ),
    )
    return parser


async def _stale_rows(stale_after_seconds: int, *, owner: str | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=stale_after_seconds)
    rows: list[dict] = []
    async with get_database() as db:
        tasks = (
            await db.execute(select(Task).where(Task.status == TaskStatus.RUNNING))
        ).scalars().all()
        for task in tasks:
            lease = (
                (task.metadata_ or {}).get("task_lease")
                if isinstance(task.metadata_, dict)
                else None
            )
            if owner and (
                not isinstance(lease, dict) or lease.get("owner") != owner
            ):
                continue
            heartbeat_at = _task_heartbeat_at(task)
            if heartbeat_at is None or heartbeat_at > cutoff:
                continue
            retry_count = int(task.retry_count or 0)
            max_retries = int(task.max_retries or 0)
            metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
            action = (
                "cancel"
                if metadata.get("cancel_requested")
                else "requeue"
                if task.task_type in RECOVERABLE_IDEMPOTENT_TASK_TYPES
                and retry_count < max_retries
                else "fail"
                if task.task_type in RECOVERABLE_IDEMPOTENT_TASK_TYPES
                else "cancel"
            )
            rows.append(
                {
                    "task_uuid": task.task_uuid,
                    "task_type": task.task_type.value,
                    "last_heartbeat_at": heartbeat_at.isoformat(),
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                    "recovery_action": action,
                }
            )
    return rows


async def _main() -> int:
    args = _parser().parse_args()
    immediate_owner_recovery = bool(
        args.confirmed_dead_owner and args.apply and args.owner
    )
    if args.stale_after_seconds < 30 and not immediate_owner_recovery:
        raise SystemExit("--stale-after-seconds must be at least 30")
    if args.confirmed_dead_owner and not immediate_owner_recovery:
        raise SystemExit(
            "--confirmed-dead-owner requires --apply and an exact --owner"
        )
    try:
        rows = await _stale_rows(args.stale_after_seconds, owner=args.owner)
        print(json.dumps({"stale_tasks": rows, "apply": args.apply}, indent=2))
        if not args.apply or not rows:
            return 0

        worker_live, registry_available = await runtime_registry.service_is_live("worker")
        if (worker_live or not registry_available) and not args.force:
            reason = "a worker is live" if worker_live else "runtime registry is unavailable"
            raise SystemExit(
                f"Refusing recovery because {reason}. Stop the worker first; use --force only after independent verification."
            )
        if args.confirmed_dead_owner:
            lease_owner, lease_registry_available = await runtime_registry.lease_owner(
                "worker"
            )
            if not lease_registry_available:
                raise SystemExit(
                    "Refusing immediate recovery because the runtime registry is unavailable."
                )
            if lease_owner is not None:
                raise SystemExit(
                    "Refusing immediate recovery because a worker lease still exists."
                )
        recovered = await recover_interrupted_tasks_on_startup(
            stale_after_seconds=args.stale_after_seconds,
            only_owner=args.owner,
        )
        print(json.dumps({"recovered": recovered}))
        return 0
    finally:
        await runtime_registry.close()
        await dispose_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
