#!/usr/bin/env python3
"""Configure half-hour US checks with bounded refreshes; preserve pauses."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from src.core.database import get_db, dispose_database
from src.domain import AutomationJob
from src.services.automation_service import automation_service
from scripts.us_configure_hiv_automation import JOB_ID as HIV_JOB_ID, JOB_VALUES as HIV_JOB_VALUES


def desired_jobs():
    return {
        "us-daily": {
            "name": "US NNDSS 30-Minute Publication Check", "country_code": "US",
            "source": "nndss_api", "enabled": True, "priority": "normal",
            "process": True, "save_raw": True, "fill_missing": False, "force": False,
            "include_current_month": False, "retry_threshold": 3,
            "interval_minutes": 30, "daily_time": None,
            "timezone": "America/New_York",
            "notes": "Half-hour discovery of CDC NNDSS weekly data with an eight-week revision lookback. "
                     "Historical archives use a separate explicit fill-missing or forced replay; "
                     "successful store receipts track subdivision archive coverage.",
        },
        HIV_JOB_ID: dict(HIV_JOB_VALUES),
    }


@dataclass(frozen=True)
class JobChange:
    job_id: str
    action: str
    enabled: bool
    changed_fields: tuple[str, ...]


async def configure(*, apply=False):
    desired = desired_jobs()
    async with get_db() as db:
        existing = {row.job_id: row for row in (await db.execute(
            select(AutomationJob).where(AutomationJob.job_id.in_(tuple(desired)))
        )).scalars().all()}
        changes = []
        for job_id, defaults in desired.items():
            values = dict(defaults)
            job = existing.get(job_id)
            if job is not None:
                values["enabled"] = job.enabled
            fields = tuple(key for key, value in values.items() if job is None or getattr(job, key) != value)
            changes.append(JobChange(job_id, "create" if job is None else "update" if fields else "unchanged",
                                     bool(values["enabled"]), fields))
            if apply and fields:
                if job is None:
                    db.add(AutomationJob(job_id=job_id, **values))
                else:
                    for key in fields:
                        setattr(job, key, values[key])
        if apply and any(change.changed_fields for change in changes):
            await db.commit()
    if apply:
        for change in changes:
            if change.changed_fields:
                await automation_service.reschedule_job(change.job_id)
    return changes


async def main_async(args):
    try:
        changes = await configure(apply=args.apply)
        receipt = {"mode": "applied" if args.apply else "preview", "jobs": [asdict(change) for change in changes]}
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    asyncio.run(main_async(parser.parse_args()))
