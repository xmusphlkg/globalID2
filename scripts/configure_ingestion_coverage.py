#!/usr/bin/env python3
"""Idempotently add ingestion jobs for implemented but unscheduled pipelines.

The command is read-only unless ``--apply`` is supplied.  It deliberately does
not create a Canada-national job: ``CA`` is a parent jurisdiction and has no
national crawler yet, while ``CA-ON`` is an implemented independent feed.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.domain import AutomationJob  # noqa: E402


JOB_VALUES = {
    "ca-on-pho-daily": {
        "name": "Ontario PHO IDTO Daily Refresh",
        "country_code": "CA-ON",
        "source": "pho_idto_monthly",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": False,
        "force": False,
        "include_current_month": False,
        "revision_window_months": 12,
        "retry_threshold": 3,
        "interval_minutes": None,
        "daily_time": "09:35",
        "timezone": "America/Toronto",
        "notes": (
            "Daily current-year PHO IDTO snapshot. Newly observed disease labels "
            "are retained in unmapped holding series for review."
        ),
    },
    "ie-hpsc-daily": {
        "name": "Ireland HPSC NDH Daily Refresh",
        "country_code": "IE",
        "source": "hpsc_ndh",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": True,
        "force": False,
        "include_current_month": False,
        "revision_window_months": 12,
        "retry_threshold": 3,
        "interval_minutes": None,
        "daily_time": "09:10",
        "timezone": "Europe/Dublin",
        "notes": (
            "Daily HPSC NDH refresh with a twelve-week revision window and "
            "missing-week reconciliation; public reuse remains permission-gated."
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure ingestion jobs for implemented unscheduled jurisdictions."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the jobs; without this flag the command is read-only.",
    )
    return parser.parse_args()


async def configure(*, apply: bool) -> None:
    async with get_database() as db:
        existing = {
            row.job_id: row
            for row in (
                await db.execute(
                    select(AutomationJob).where(
                        AutomationJob.job_id.in_(tuple(JOB_VALUES))
                    )
                )
            ).scalars()
        }
        actions: list[tuple[str, str, int]] = []
        for job_id, desired in JOB_VALUES.items():
            job = existing.get(job_id)
            action = "create" if job is None else "update"
            changed = (
                len(desired)
                if job is None
                else sum(
                    getattr(job, field) != value for field, value in desired.items()
                )
            )
            actions.append((job_id, action, changed))
            if not apply:
                continue
            if job is None:
                db.add(AutomationJob(job_id=job_id, **desired))
            else:
                for field, value in desired.items():
                    setattr(job, field, value)
        if apply:
            await db.commit()

    verb = "Applied" if apply else "Plan"
    for job_id, action, changed in actions:
        print(f"{verb}: {action} {job_id}; pending_fields={changed}")


def main() -> None:
    args = parse_args()
    asyncio.run(configure(apply=args.apply))


if __name__ == "__main__":
    main()
