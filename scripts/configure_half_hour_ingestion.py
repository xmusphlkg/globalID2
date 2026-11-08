#!/usr/bin/env python3
"""Spread enabled ingestion jobs over a 30-minute cycle.

Run with the scheduler stopped: its schedule is cached in memory. The default
is a read-only plan; --apply changes only enabled jobs and their next run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import get_database
from src.domain import AutomationJob, ScheduledJobState

INTERVAL = 30


def assign_minutes(jobs):
    """Balance upstreams and avoid scheduling a country's sources together."""
    upstream_sizes = Counter(job.source.lower() for job in jobs)
    ordered = sorted(
        jobs,
        key=lambda j: (
            -upstream_sizes[j.source.lower()],
            j.source.lower(),
            j.country_code,
            j.job_id,
        ),
    )
    counts = [0] * INTERVAL
    source_counts = [Counter() for _ in range(INTERVAL)]
    country_slots: dict[str, list[int]] = {}
    result = {}
    for job in ordered:
        source, country = job.source.lower(), job.country_code.upper()
        preferred = (
            int.from_bytes(hashlib.sha256(job.job_id.encode()).digest()[:4], "big")
            % INTERVAL
        )

        def score(slot, source=source, country=country, preferred=preferred):
            distances = [
                min((slot - other) % INTERVAL, (other - slot) % INTERVAL)
                for other in country_slots.get(country, [])
            ]
            return (
                source_counts[slot][source],
                max(0, 5 - min(distances, default=5)),
                counts[slot],
                (slot - preferred) % INTERVAL,
            )

        slot = min(range(INTERVAL), key=score)
        result[job.job_id] = slot
        counts[slot] += 1
        source_counts[slot][source] += 1
        country_slots.setdefault(country, []).append(slot)
    return result


def next_slot(now: datetime, minute: int) -> datetime:
    """Next phase boundary strictly after now, in UTC."""
    current = now.astimezone(timezone.utc)
    base = current.replace(minute=0, second=0, microsecond=0)
    candidate = base + timedelta(
        minutes=minute + (INTERVAL if current.minute >= INTERVAL else 0)
    )
    if candidate <= current:
        candidate += timedelta(minutes=INTERVAL)
    return candidate


async def configure(apply: bool):
    async with get_database() as db:
        jobs = list(
            (
                await db.execute(
                    select(AutomationJob).where(AutomationJob.enabled.is_(True))
                )
            ).scalars()
        )
        phases = assign_minutes(jobs)
        states = {
            row.job_id: row
            for row in (
                await db.execute(
                    select(ScheduledJobState).where(
                        ScheduledJobState.job_kind == "ingestion"
                    )
                )
            ).scalars()
        }
        now = datetime.now(timezone.utc)
        changed = 0
        for job in sorted(jobs, key=lambda j: (phases[j.job_id], j.job_id)):
            state = states.get(job.job_id)
            needs_update = (
                job.interval_minutes != INTERVAL
                or job.daily_time is not None
                or state is None
                or state.next_run_at is None
            )
            when = (
                next_slot(now, phases[job.job_id])
                if needs_update
                else state.next_run_at
            )
            print(
                f"{job.job_id:35} {job.country_code:7} {job.source:24} minute={phases[job.job_id]:02d}/{phases[job.job_id] + 30:02d} next={when.isoformat()} {'change' if needs_update else 'unchanged'}"
            )
            if apply and needs_update:
                job.interval_minutes = INTERVAL
                job.daily_time = None
                if state is None:
                    state = ScheduledJobState(
                        job_kind="ingestion", job_id=job.job_id, last_status="idle"
                    )
                    db.add(state)
                state.next_run_at = when
            changed += int(needs_update)
        if apply:
            await db.commit()
        print(
            f"{'Applied' if apply else 'Planned'}: {len(jobs)} enabled jobs, {changed} changed"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="save changes; stop scheduler first"
    )
    args = parser.parse_args()
    asyncio.run(configure(args.apply))


if __name__ == "__main__":
    main()
