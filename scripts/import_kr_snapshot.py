#!/usr/bin/env python3
"""Import a reviewed Korea historical CSV through the production ingestion path."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core.database import dispose_database, get_db
from src.core.task_manager import task_manager
from src.data.processors.kr import KRMonthlyUpdater
from src.domain import Country, Task
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import kr
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance


async def run(args: argparse.Namespace) -> dict:
    source_file = args.source_file.resolve()
    if not source_file.exists():
        raise FileNotFoundError(source_file)
    months = [
        (year, month)
        for year in range(args.start_year, args.end_year + 1)
        for month in range(1, 13)
    ]
    async with get_db() as db:
        country_id = (
            await db.execute(select(Country.id).where(Country.code == "KR"))
        ).scalar_one()
    updater = KRMonthlyUpdater()
    updater.mapping_notifications_enabled = False
    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name=f"KR KDCA historical import {args.start_year}-{args.end_year}",
        country_id=country_id,
        priority=TaskPriority.NORMAL,
        status=TaskStatus.RUNNING,
        input_data={
            "country_code": "KR",
            "source": "kdca_open_api",
            "source_file": source_file.as_posix(),
            "start_year": args.start_year,
            "end_year": args.end_year,
            "manual_trigger": True,
            "mapping_notifications_enabled": False,
        },
        tags=["kr-kdca", "foreground", "historical-import"],
    )
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(
                CrawlService(),
                task=task,
                updater=updater,
                connector=kr,
                source="kdca_open_api",
                force=True,
                process=True,
                connector_options={
                    "months": months,
                    "history_csv": updater.output_csv,
                    "source_file": source_file,
                },
            )
        output = {"task_uuid": task.task_uuid, **asdict(result)}
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.output_data = output
            saved.status = TaskStatus.COMPLETED
            saved.progress = 100
            saved.completed_at = datetime.now(timezone.utc)
        return output
    except BaseException as exc:
        if lease is not None:
            async with get_db() as db:
                saved = await lease.owned_task(db)
                saved.status = TaskStatus.FAILED
                saved.last_error = str(exc)
                saved.completed_at = datetime.now(timezone.utc)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-file",
        type=Path,
        default=Path("data/current/kr/korea_national_monthly_historical.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2001)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("--start-year must be no greater than --end-year")

    async def execute() -> None:
        try:
            print(json.dumps(await run(args), ensure_ascii=False))
        finally:
            await dispose_database()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
