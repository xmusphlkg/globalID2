#!/usr/bin/env python3
"""Configure the 19 PHF Science annual DHB refresh jobs.

The existing NZ national job reads monthly data. Each historical DHB has a
separate annual pipeline identity, so its update must have its own scheduler
entry. This command previews changes unless --apply is supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.data.crawlers.nz import DHB_JURISDICTIONS  # noqa: E402
from src.domain import AutomationJob  # noqa: E402


def desired_jobs() -> dict[str, dict[str, object]]:
    """Stagger source refreshes so all DHBs do not fetch together."""

    jobs: dict[str, dict[str, object]] = {}
    for index, (area, country_code) in enumerate(sorted(DHB_JURISDICTIONS.items())):
        minutes = 10 * 60 + index * 10
        jobs[f"nz-dhb-{country_code[3:].lower()}-daily"] = {
            "name": f"New Zealand PHF Annual DHB {area} Refresh",
            "country_code": country_code,
            "source": "phf_dhb_annual",
            "enabled": True,
            "priority": "normal",
            "process": True,
            "save_raw": True,
            "fill_missing": False,
            "force": False,
            "include_current_month": False,
            "revision_window_months": 3,
            "retry_threshold": 3,
            "interval_minutes": None,
            "daily_time": f"{minutes // 60:02d}:{minutes % 60:02d}",
            "timezone": "Pacific/Auckland",
            "notes": (
                "Daily availability check for the PHF Science annual DHB total "
                "dataset. Annual source observations are refreshed without "
                "merging them into national monthly data."
            ),
        }
    return jobs


async def configure(*, apply: bool) -> list[tuple[str, str, int]]:
    desired = desired_jobs()
    async with get_database() as db:
        existing = {
            row.job_id: row
            for row in (
                await db.execute(
                    select(AutomationJob).where(AutomationJob.job_id.in_(tuple(desired)))
                )
            ).scalars()
        }
        actions: list[tuple[str, str, int]] = []
        for job_id, values in desired.items():
            job = existing.get(job_id)
            changed = (
                len(values)
                if job is None
                else sum(getattr(job, field) != value for field, value in values.items())
            )
            actions.append((job_id, "create" if job is None else "update", changed))
            if not apply:
                continue
            if job is None:
                db.add(AutomationJob(job_id=job_id, **values))
            else:
                for field, value in values.items():
                    setattr(job, field, value)
        if apply:
            await db.commit()
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the jobs")
    args = parser.parse_args()
    for job_id, action, changed in asyncio.run(configure(apply=args.apply)):
        print(f"{action} {job_id}; changed_fields={changed}")


if __name__ == "__main__":
    main()
