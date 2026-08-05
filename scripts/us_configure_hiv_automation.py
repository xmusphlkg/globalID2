#!/usr/bin/env python3
"""Idempotently enable the production US NHSS HIV refresh job.

NNDSS and NHSS have different publication cadences and failure domains. This
script deliberately leaves the existing US NNDSS daily job unchanged and adds
an independent weekly check for the annual NHSS release.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.domain import AutomationJob  # noqa: E402
from src.services.automation_service import automation_service  # noqa: E402


JOB_ID = "us-nhss-hiv-weekly"
JOB_VALUES = {
    "name": "US NHSS HIV Weekly Check",
    "country_code": "US",
    "source": "nhss_hiv",
    "enabled": True,
    "priority": "normal",
    "process": True,
    "save_raw": True,
    "fill_missing": True,
    "force": False,
    "retry_threshold": 3,
    "interval_minutes": 7 * 24 * 60,
    "daily_time": None,
    "timezone": "America/New_York",
    "notes": (
        "Independent weekly discovery of the annual CDC NHSS HIV release; "
        "kept separate from the NNDSS daily job to isolate source failures."
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the independent US CDC NHSS HIV automation job."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the job; without this flag the command is read-only.",
    )
    parser.add_argument(
        "--trigger",
        action="store_true",
        help="Queue an immediate validation run after applying the job.",
    )
    return parser.parse_args()


async def configure(*, apply: bool, trigger: bool) -> None:
    if trigger and not apply:
        raise ValueError("--trigger requires --apply")

    async with get_database() as db:
        job = (
            await db.execute(
                select(AutomationJob).where(AutomationJob.job_id == JOB_ID)
            )
        ).scalar_one_or_none()

        action = "create" if job is None else "update"
        changes: dict[str, tuple[object, object]] = {}
        if job is not None:
            for field, desired in JOB_VALUES.items():
                current = getattr(job, field)
                if current != desired:
                    changes[field] = (current, desired)

        if not apply:
            print(f"Plan: {action} automation job {JOB_ID}")
            print(f"Source: {JOB_VALUES['source']}")
            print(f"Interval minutes: {JOB_VALUES['interval_minutes']}")
            print(f"Pending field changes: {len(changes)}")
            return

        if job is None:
            job = AutomationJob(job_id=JOB_ID, **JOB_VALUES)
            db.add(job)
        else:
            for field, desired in JOB_VALUES.items():
                setattr(job, field, desired)
        await db.commit()

    print(f"Applied automation job {JOB_ID} ({action})")
    if trigger:
        result = await automation_service.trigger_job(JOB_ID, manual=True)
        print(
            "Validation task: "
            f"status={result['status']} task_uuid={result.get('task_uuid') or '-'}"
        )


def main() -> None:
    args = parse_args()
    asyncio.run(configure(apply=args.apply, trigger=args.trigger))


if __name__ == "__main__":
    main()
