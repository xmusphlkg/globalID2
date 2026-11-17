#!/usr/bin/env python3
"""Import the explicit official iDengue daily snapshot with a foreground lease."""
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
from src.data.crawlers.my_dengue import DEFAULT_SCOPE
from src.data.processors.my_dengue import MalaysiaIDengueDailyUpdater
from src.domain import Country, CrawlRun, Task
from src.domain.collection import RunStage, SourceArtifact
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries import my_dengue
from src.services.ingestion.foreground import (
    ForegroundTaskLeaseLostError,
    foreground_task_lease,
)
from src.services.ingestion.runner import run_surveillance


class CapturedReplay:
    def __init__(self, original_task_uuid, artifacts):
        self.original_task_uuid, self.artifacts = original_task_uuid, artifacts

    async def collect(self, writer, *, source, **options):
        MalaysiaIDengueDailyUpdater.normalize_source(source)
        for artifact in self.artifacts:
            await writer.write(read_artifact(artifact), source_url=artifact.source_url, kind=artifact.kind,
                metadata={**artifact.metadata_, "replayed_from_task_uuid": self.original_task_uuid,
                          "replayed_from_artifact_id": artifact.id})
        return writer.artifacts

    organize = staticmethod(my_dengue.organize)


async def run(args):
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(Country.code == "MY"))).scalar_one()
    updater = MalaysiaIDengueDailyUpdater()
    connector = my_dengue
    if args.replay_task_uuid:
        async with get_db() as db:
            original = (await db.execute(select(Task).where(Task.task_uuid == args.replay_task_uuid))).scalar_one()
            if (original.input_data or {}).get("source") != DEFAULT_SCOPE:
                raise ValueError("Replay task is not an iDengue daily source collection")
            originals = (await db.execute(select(SourceArtifact).where(
                SourceArtifact.task_uuid == args.replay_task_uuid).order_by(SourceArtifact.id))).scalars().all()
        if sum(a.kind == "my_idengue_html" for a in originals) != 1:
            raise ValueError("Replay task must contain exactly one captured iDengue HTML artifact")
        connector = CapturedReplay(args.replay_task_uuid, originals)
    task = await task_manager.create_task(task_type=TaskType.CRAWL_DATA,
        task_name="Malaysia iDengue explicit daily snapshot", country_id=country_id,
        priority=TaskPriority.NORMAL, status=TaskStatus.RUNNING,
        input_data={"country_code": "MY", "source": DEFAULT_SCOPE, "manual_trigger": True,
                    "mapping_notifications_enabled": False, "replayed_from_task_uuid": args.replay_task_uuid},
        tags=["my-idengue-daily", "foreground"])
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
    print(json.dumps({"task_uuid": task.task_uuid, "source": DEFAULT_SCOPE}), flush=True)
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=connector, source=DEFAULT_SCOPE, force=False, process=True)
        async with get_db() as db:
            stage = (await db.execute(select(RunStage).where(RunStage.task_uuid == task.task_uuid,
                RunStage.stage == "organize").order_by(RunStage.attempt.desc()))).scalars().first()
            output = {"task_uuid": task.task_uuid, **asdict(result),
                      "source_audit": (stage.metrics or {}).get("source_audit", {})}
            saved = await lease.owned_task(db)
            saved.output_data, saved.status, saved.progress = output, TaskStatus.COMPLETED, 100
            saved.completed_at = datetime.now(timezone.utc)
            if saved.started_at:
                started = saved.started_at.replace(tzinfo=timezone.utc) if saved.started_at.tzinfo is None else saved.started_at
                saved.actual_duration = int((saved.completed_at - started).total_seconds())
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(output, ensure_ascii=False), flush=True)
    except ForegroundTaskLeaseLostError:
        raise
    except BaseException as exc:
        if lease is None:
            raise
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.status, saved.last_error = TaskStatus.FAILED, str(exc)
            saved.completed_at = datetime.now(timezone.utc)
            runs = (await db.execute(select(CrawlRun).where(CrawlRun.country_code == "MY",
                CrawlRun.status == "running", CrawlRun.raw_dir.like(f"%/{task.task_uuid}/%")))).scalars().all()
            for crawl_run in runs:
                crawl_run.status = "failed"
                crawl_run.finished_at = datetime.now(timezone.utc)
                crawl_run.error_message = str(exc)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-task-uuid")
    parser.add_argument("--result-json", type=Path, default=ROOT / "data/current/my/idengue_import_result.json")
    args = parser.parse_args()
    async def execute():
        try:
            await run(args)
        finally:
            await dispose_database()
    asyncio.run(execute())


if __name__ == "__main__":
    main()
