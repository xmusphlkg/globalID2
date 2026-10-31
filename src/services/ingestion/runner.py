"""Shared collect → organize → store runner; source policies stay outside it."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.core.database import get_db
from src.core.task_manager import task_manager
from src.domain import CrawlRun
from src.services.ingestion.artifacts import ArtifactWriter
from src.services.ingestion.stages import StageRun
from src.services.ingestion.strategies import UpdaterCommitStrategy
from src.services.exceptions import TaskCancelledError


async def run_surveillance(service, *, task, updater, connector, source, force, process, strategy=None, strategy_options=None, connector_options=None):
    from src.services.crawl_service import CrawlResult

    stages = await StageRun.start(task.task_uuid, "surveillance")
    writer = ArtifactWriter(task.task_uuid, stages.attempt, updater.country_code.lower())
    async with get_db() as db:
        run = CrawlRun(country_code=updater.country_code, source=source, status="running", started_at=datetime.now(timezone.utc), raw_dir=writer.directory.as_posix(), metadata_={"force": force, "process": process, "attempt": stages.attempt})
        db.add(run)
        await db.flush()
        run_id = run.id

    async def cancelled():
        if await task_manager.is_cancel_requested(task.task_uuid):
            raise TaskCancelledError("Cancellation requested by user")

    try:
        await stages.transition("collect")
        await cancelled()
        artifacts = await connector.collect(writer, source=source, **(connector_options or {}))
        await stages.finish(metrics={"artifact_count": len(artifacts), "artifact_bytes": sum(a.byte_size for a in artifacts)})
        await stages.transition("organize")
        await cancelled()
        fetched = await asyncio.to_thread(connector.organize, artifacts, updater=updater, source=source, **(connector_options or {}))
        organize_metrics = {"rows": len(fetched.rows), "series_rows": len(getattr(fetched, "series_rows", []))}
        source_audit = getattr(fetched, "source_audit", None)
        if source_audit:
            organize_metrics["source_audit"] = source_audit
        await stages.finish(metrics=organize_metrics)
        await stages.transition("store")
        await cancelled()
        imported = 0
        if process:
            async with get_db() as db:
                result = await (strategy or UpdaterCommitStrategy()).commit(db, service=service, updater=updater, fetched=fetched, force=force, task_uuid=task.task_uuid, **(strategy_options or {}))
                imported = result.inserted_or_updated
        await stages.finish(metrics={"rows": imported, "process": process})
        await service._finish_crawl_run(run_id, new_reports=imported, processed=int(process), records=imported)
        await task_manager.update_task_progress(task.task_uuid, 100)
        return CrawlResult(imported, int(process), imported, run_id)
    except BaseException as exc:
        await stages.finish(error=str(exc) or type(exc).__name__)
        raise
