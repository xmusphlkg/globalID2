#!/usr/bin/env python3
"""Idempotently configure Iceland surveillance refresh jobs.

The command is read-only unless ``--apply`` is supplied. Live Power BI
dashboards refresh daily; immutable historical workbook catalogues are checked
less frequently for official corrections while retaining their source grain.
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
    "is-doh-live-daily": {
        "name": "Iceland Directorate of Health Daily Refresh",
        "country_code": "IS",
        "source": "all",
        "enabled": True,
        "priority": "normal",
        "process": True,
        "save_raw": True,
        "fill_missing": False,
        "force": False,
        "retry_threshold": 3,
        "interval_minutes": None,
        "daily_time": "06:30",
        "timezone": "Atlantic/Reykjavik",
        "notes": (
            "Daily refresh of annual, STI monthly, and respiratory ISO-week "
            "Power BI facts; authoritative revisions are upserted."
        ),
    },
    "is-doh-history-monthly": {
        "name": "Iceland Historical Registry Monthly Check",
        "country_code": "IS",
        "source": "is_doh_history",
        "enabled": True,
        "priority": "low",
        "process": True,
        "save_raw": True,
        "fill_missing": False,
        "force": False,
        "retry_threshold": 3,
        "interval_minutes": 30 * 24 * 60,
        "daily_time": None,
        "timezone": "Atlantic/Reykjavik",
        "notes": (
            "Monthly correction check for the official annual registry and "
            "disease-specific monthly workbooks; blanks remain unknown."
        ),
    },
    "is-doh-legacy-icd-quarterly": {
        "name": "Iceland Legacy ICD Quarterly Check",
        "country_code": "IS",
        "source": "is_doh_legacy_icd",
        "enabled": True,
        "priority": "low",
        "process": True,
        "save_raw": True,
        "fill_missing": False,
        "force": False,
        "retry_threshold": 3,
        "interval_minutes": 90 * 24 * 60,
        "daily_time": None,
        "timezone": "Atlantic/Reykjavik",
        "notes": (
            "Quarterly provenance check for legacy Saga ICD registered-"
            "diagnosis workbooks; never merged into notification trends."
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure Iceland live and historical crawl jobs."
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
