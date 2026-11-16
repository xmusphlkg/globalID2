#!/usr/bin/env python3
"""Persist verified surveillance channels as control-panel jobs.

Preview by default. --apply writes only catalogue jobs, preserving operator
pauses. Sources are checked daily; the observations retain source cadence.
"""
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
from src.core.source_channels import load_source_channels
from src.domain import AutomationJob
from src.services.automation_service import automation_service


def refresh_note(channel):
    strategy = channel['refresh_strategy']
    if strategy == 'metadata_version_check_requires_verified_full_history':
        return ('Daily metadata and year-count check against independently verified full history. '
                'A changed version requires the resumable full backfill and verification; '
                'the check reports requires_full_backfill without importing a partial page. ')
    if strategy == 'full_published_history_upsert':
        return 'Full published history is reread for revisions. '
    if strategy == 'full_live_directory_and_verified_reports':
        return 'Discover the live directory and reread all reports in its verified source window; audit archive gaps. '
    return (f"Daily discovery with {channel['refresh_years']} calendar years of revision replay. "
            'Older history uses a separate full replay. ')


def desired_jobs():
    return {
        f"{channel['country_code'].lower()}-{channel['scope'].replace('_', '-')}-daily": {
            'name': f"{channel['country_code']} Official Surveillance Publication Check",
            'country_code': channel['country_code'], 'source': channel['scope'],
            'enabled': channel.get('enabled_by_default', True), 'priority': 'normal', 'process': True, 'save_raw': True,
            'fill_missing': False, 'force': False, 'include_current_month': False,
            # The legacy field name is shared by the API; the source policy
            # declares that its value counts calendar years for these channels.
            'revision_window_months': channel['refresh_years'], 'retry_threshold': 3,
            'interval_minutes': None, 'daily_time': channel['daily_time'],
            'timezone': channel['timezone'],
            'notes': f"Official channel: {channel['official_url']}\nArchive: {channel['archive_url']}\n"
                     + refresh_note(channel)
                     + "Keep source-native definitions, measures, periods, geography, explicit missingness and raw evidence. "
                     "Internal holding data; public release requires validated mappings and snapshots.",
        }
        for channel in load_source_channels()
    }


@dataclass(frozen=True)
class JobChange:
    job_id: str
    action: str
    enabled: bool
    changed_fields: tuple[str, ...]


async def configure(*, apply=False, enable=False, countries=None):
    desired = desired_jobs()
    if countries is not None:
        codes = {str(code).upper() for code in countries}
        desired = {key: value for key, value in desired.items() if value["country_code"] in codes}
    async with get_db() as db:
        existing = {row.job_id: row for row in (await db.execute(select(AutomationJob).where(
            AutomationJob.job_id.in_(tuple(desired))))).scalars().all()}
        changes = []
        for job_id, defaults in desired.items():
            values = dict(defaults)
            job = existing.get(job_id)
            if enable:
                values['enabled'] = True
            elif job is not None:
                values['enabled'] = job.enabled
            fields = tuple(key for key, value in values.items() if job is None or getattr(job, key) != value)
            changes.append(JobChange(job_id, 'create' if job is None else 'update' if fields else 'unchanged', bool(values['enabled']), fields))
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
        changes = await configure(apply=args.apply, enable=args.enable, countries=args.countries)
        receipt = {'mode': 'applied' if args.apply else 'preview', 'jobs': [asdict(x) for x in changes]}
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--enable', action='store_true', help='Resume paused managed jobs explicitly')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--countries', nargs='+', help='Only configure these countries; preserve other jobs')
    asyncio.run(main_async(parser.parse_args()))


if __name__ == '__main__':
    main()
