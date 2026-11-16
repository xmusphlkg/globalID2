#!/usr/bin/env python3
"""Foreground RIVM weekly collection/import with a live task lease."""
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
from src.data.crawlers.nl_weekly import RESP_SCOPE, VIRO_SCOPE
from src.data.processors.nl_weekly import (
    NLRespiratoryWeeklyUpdater,
    NLVirologicalWeeklyUpdater,
)
from src.domain import Country, Task
from src.domain.collection import RunStage, SourceArtifact
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries import nl_weekly
from src.services.ingestion.foreground import (
    ForegroundTaskLeaseLostError,
    foreground_task_lease,
)
from src.services.ingestion.runner import run_surveillance


class CapturedReplay:
    def __init__(self, original_task_uuid, artifacts):
        self.original_task_uuid, self.artifacts = original_task_uuid, artifacts

    async def collect(self, writer, **kwargs):
        for artifact in self.artifacts:
            await writer.write(read_artifact(artifact), source_url=artifact.source_url,
                kind=artifact.kind, metadata={**artifact.metadata_,
                    "replayed_from_task_uuid": self.original_task_uuid,
                    "replayed_from_artifact_id": artifact.id})
        return writer.artifacts

    organize = staticmethod(nl_weekly.organize)


async def run(args):
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(Country.code == "NL"))).scalar_one()
    updater = (NLVirologicalWeeklyUpdater if args.source == VIRO_SCOPE else NLRespiratoryWeeklyUpdater)()
    connector = nl_weekly
    if args.replay_task_uuid:
        async with get_db() as db:
            original = (await db.execute(select(Task).where(Task.task_uuid == args.replay_task_uuid))).scalar_one()
            if (original.input_data or {}).get("source") != args.source:
                raise ValueError("Replay task must use the same RIVM weekly source")
            originals = (await db.execute(select(SourceArtifact).where(
                SourceArtifact.task_uuid == args.replay_task_uuid).order_by(SourceArtifact.id))).scalars().all()
        connector = CapturedReplay(args.replay_task_uuid, originals)
    task = await task_manager.create_task(task_type=TaskType.CRAWL_DATA,
        task_name=f"RIVM weekly source refresh: {args.source}", country_id=country_id,
        priority=TaskPriority.NORMAL, status=TaskStatus.RUNNING,
        input_data={"country_code": "NL", "source": args.source,
            "manual_trigger": True, "mapping_notifications_enabled": False,
            "replayed_from_task_uuid": args.replay_task_uuid}, tags=["rivm-weekly", "foreground"])
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
    print(json.dumps({"task_uuid": task.task_uuid, "source": args.source}), flush=True)
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=connector, source=args.source, force=False, process=True)
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
        target = args.result_json or ROOT / f"data/current/nl/{args.source}_import_result.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
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
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=[VIRO_SCOPE, RESP_SCOPE], required=True)
    parser.add_argument("--replay-task-uuid")
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args()
    async def execute():
        try:
            await run(args)
        finally:
            await dispose_database()
    asyncio.run(execute())


if __name__ == "__main__":
    main()
