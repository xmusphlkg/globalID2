"""Run a queued Malawi IDSR task through the fenced foreground staged pipeline."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select

from src.core.database import dispose_database, get_db
from src.core.task_manager import task_manager
from src.data.processors.mw import MalawiPHIMIDSRUpdater
from src.domain import Country, Task, TaskPriority, TaskType
from src.domain.collection import SourceArtifact
from src.domain.task import TaskStatus
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import mw
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance


class CapturedReplay:
    def __init__(self, originals):
        self.originals = originals

    async def collect(self, writer, **kwargs):
        for artifact in self.originals:
            await writer.write(read_artifact(artifact), source_url=artifact.source_url,
                               kind=artifact.kind, metadata=artifact.metadata_)
        return writer.artifacts

    organize = staticmethod(mw.organize)


async def run(task_uuid: str | None, *, replay_from: str | None = None):
    connector = mw
    if replay_from:
        async with get_db() as db:
            original = (await db.execute(select(Task).where(Task.task_uuid == replay_from))).scalar_one()
            if original.status != TaskStatus.COMPLETED or (original.input_data or {}).get("source") != mw.DEFAULT_SCOPE:
                raise ValueError("Replay requires a completed Malawi IDSR task")
            originals = (await db.execute(select(SourceArtifact).where(
                SourceArtifact.task_uuid == replay_from).order_by(SourceArtifact.id))).scalars().all()
            country_id = (await db.execute(select(Country.id).where(Country.code == "MW"))).scalar_one()
        if not originals:
            raise ValueError("Replay task has no captured originals")
        connector = CapturedReplay(originals)
        task = await task_manager.create_task(task_type=TaskType.CRAWL_DATA,
            task_name="Malawi PHIM IDSR offline archive replay", country_id=country_id,
            priority=TaskPriority.NORMAL, status=TaskStatus.RUNNING,
            input_data={"country_code": "MW", "source": mw.DEFAULT_SCOPE,
                        "start_year": mw.HISTORY_START_YEAR, "process": True,
                        "replayed_from_task_uuid": replay_from},
            tags=["mw-idsr", "foreground", "offline-replay"])
        task_uuid = task.task_uuid
    else:
        if not task_uuid:
            raise ValueError("Provide a queued task UUID or --replay-from")
        task = await task_manager.get_task_by_uuid(task_uuid)
        if task is None or task.status != TaskStatus.QUEUED:
            raise ValueError("A queued Malawi crawl task is required")
        inputs = task.input_data or {}
        if inputs.get("country_code") != "MW" or inputs.get("source") != mw.DEFAULT_SCOPE:
            raise ValueError("Task is not for the Malawi PHIM IDSR source")
        await task_manager.update_task_status(task_uuid, TaskStatus.RUNNING)
    inputs = task.input_data or {}
    if replay_from:
        await task_manager.update_task_status(task_uuid, TaskStatus.RUNNING)
    lease = None
    try:
        async with foreground_task_lease(task_uuid) as lease:
            result = await run_surveillance(
                CrawlService(), task=task, updater=MalawiPHIMIDSRUpdater(), connector=connector,
                source=mw.DEFAULT_SCOPE, force=bool(inputs.get("force")),
                process=bool(inputs.get("process", True)),
                connector_options={"start_year": int(inputs.get("start_year") or mw.HISTORY_START_YEAR),
                                   "force": bool(inputs.get("force"))},
            )
        output = asdict(result)
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.output_data = output
            saved.status = TaskStatus.COMPLETED
            saved.progress = 100
            saved.completed_at = datetime.now(timezone.utc)
        print({"task_uuid": task_uuid, **output}, flush=True)
    except BaseException as exc:
        if lease is not None:
            async with get_db() as db:
                saved = await lease.owned_task(db)
                saved.status = TaskStatus.FAILED
                saved.last_error = str(exc)
                saved.completed_at = datetime.now(timezone.utc)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_uuid", nargs="?", help="Existing queued Malawi crawl task UUID")
    parser.add_argument("--replay-from", help="Completed Malawi task whose originals are replayed offline")
    args = parser.parse_args()
    if bool(args.task_uuid) == bool(args.replay_from):
        parser.error("Provide either a queued task UUID or --replay-from")

    async def execute():
        try:
            await run(args.task_uuid, replay_from=args.replay_from)
        finally:
            await dispose_database()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
