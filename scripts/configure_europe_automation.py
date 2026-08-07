#!/usr/bin/env python3
"""Idempotently configure dynamic FI/NO/SE surveillance refresh jobs.

The command is read-only unless ``--apply`` is supplied.  Sweden's ingestion
job is intentionally independent of the public-release gate: data can be
validated internally while ``country_bootstrap.SE.public_release_enabled``
remains false pending reuse confirmation.
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


DAILY_MINUTES = 24 * 60
JOB_VALUES = {
    "fi-thl-weekly": {
        "name": "Finland THL Daily Dynamic Refresh",
        "country_code": "FI",
        "source": "thl_ttr",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": True,
        "force": False,
        "include_current_month": True,
        "revision_window_months": 3,
        "retry_threshold": 3,
        "interval_minutes": DAILY_MINUTES,
        "daily_time": None,
        "timezone": "Europe/Helsinki",
        "notes": (
            "Daily provisional-current-month refresh of the THL register; the "
            "latest three months are re-fetched and authoritative revisions upserted."
        ),
    },
    "no-msis-weekly": {
        "name": "Norway FHI MSIS Daily Dynamic Refresh",
        "country_code": "NO",
        "source": "fhi_msis",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": True,
        "force": False,
        "include_current_month": True,
        "revision_window_months": 3,
        "retry_threshold": 3,
        "interval_minutes": DAILY_MINUTES,
        "daily_time": None,
        "timezone": "Europe/Oslo",
        "notes": (
            "Daily FHI MSIS refresh including the provisional current month; the "
            "latest three months are re-fetched and revised values upserted."
        ),
    },
    "se-sminet-weekly-internal": {
        "name": "Sweden SmiNet Daily Dynamic Internal Refresh",
        "country_code": "SE",
        "source": "fohm_sminet",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": True,
        "force": False,
        "include_current_month": True,
        "revision_window_months": 3,
        "retry_threshold": 3,
        "interval_minutes": DAILY_MINUTES,
        "daily_time": None,
        "timezone": "Europe/Stockholm",
        "notes": (
            "Internal daily SmiNet refresh of the latest three months. Open-month "
            "rows require non-placeholder source evidence; public export remains disabled."
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure dynamic FI, NO, and internal SE crawl jobs."
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
                else sum(getattr(job, field) != value for field, value in desired.items())
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
