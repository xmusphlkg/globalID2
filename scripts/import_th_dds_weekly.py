#!/usr/bin/env python3
"""Replay an integrity-checked DDS raw manifest through staged ingestion."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core.database import dispose_database, get_db
from src.data.crawlers.th_dds import DEFAULT_SCOPE
from src.data.processors.th_dds import ThailandDDSWeeklyUpdater
from src.domain import Country, CrawlRun, Task
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries import th_dds
from src.services.ingestion.runner import run_surveillance
from src.services.ingestion.foreground import foreground_task_lease, ForegroundTaskLeaseLostError


class LocalDDSConnector:
    def __init__(self, manifest):
        self.artifacts = [SimpleNamespace(**item) for item in json.loads(Path(manifest).read_text())]
        self.last_audit = None

    async def collect(self, writer, **options):
        th_dds.normalize_source(options["source"])
        for artifact in self.artifacts:
            payload = await asyncio.to_thread(read_artifact, artifact)
            await writer.write(payload, source_url=artifact.source_url, kind=artifact.kind,
                               metadata=artifact.metadata_)
        return writer.artifacts

    def organize(self, artifacts, **options):
        fetched = th_dds.organize(artifacts, **options)
        self.last_audit = fetched.source_audit
        return fetched


async def run(manifest):
    updater = ThailandDDSWeeklyUpdater()
    connector = LocalDDSConnector(manifest)
    # Validate every captured byte and the entire report contract before
    # creating a task or writing source facts to the database.
    reviewed = await asyncio.to_thread(connector.organize, connector.artifacts,
                                      updater=updater, source=DEFAULT_SCOPE, basis="onset")
    if reviewed.source_audit["status"] != "validated":
        raise ValueError("Manual DDS replay requires a fully validated source inventory")
    started = datetime.now(timezone.utc)
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(Country.code == "TH"))).scalar_one()
        task = Task(task_uuid=str(uuid4()), task_type=TaskType.CRAWL_DATA,
            task_name="Thailand DDS all-disease weekly source replay", country_id=country_id,
            priority=TaskPriority.NORMAL, status=TaskStatus.RUNNING, started_at=started,
            input_data={"country_code": "TH", "source": DEFAULT_SCOPE, "manual_trigger": True,
                "raw_manifest": str(manifest), "mapping_notifications_enabled": False,
                "source_audit": reviewed.source_audit},
            tags=["th-dds", "foreground", "downloaded-source-replay"])
        db.add(task)
        await db.flush()
    print(json.dumps({"task_uuid": task.task_uuid, "status": "running"}), flush=True)
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=connector, source=DEFAULT_SCOPE, force=False, process=True,
                connector_options={"basis": "onset"})
        output = {**asdict(result), "source_audit": connector.last_audit}
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.status = TaskStatus.COMPLETED
            saved.progress = 100
            saved.completed_at = datetime.now(timezone.utc)
            saved.actual_duration = int((saved.completed_at - started).total_seconds())
            saved.output_data = output
        return {"task_uuid": task.task_uuid, "status": "completed", **output}
    except ForegroundTaskLeaseLostError:
        raise
    except BaseException as exc:
        if lease is None:
            raise
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.status = TaskStatus.FAILED
            saved.completed_at = datetime.now(timezone.utc)
            saved.last_error = str(exc)
            runs = (await db.execute(select(CrawlRun).where(CrawlRun.country_code == "TH",
                CrawlRun.status == "running", CrawlRun.raw_dir.like(f"%/{task.task_uuid}/%")))).scalars()
            for crawl in runs:
                crawl.status = "failed"
                crawl.finished_at = saved.completed_at
                crawl.error_message = str(exc)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args()

    async def execute():
        try:
            result = await run(args.manifest)
            if args.result_json:
                args.result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            await dispose_database()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
