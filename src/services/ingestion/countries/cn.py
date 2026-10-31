"""National China CDC report collection and independent report commits."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.core.database import get_db
from src.core.task_manager import task_manager
from src.data.crawlers.cn import ChinaCDCCrawler
from src.data.crawlers.base import CrawlerResult
from src.data.processors.cn import DataProcessor
from src.data.parsers.html_parser import HTMLTableParser
from src.domain import CrawlRun
from src.services.ingestion.artifacts import ArtifactWriter, read_artifact
from src.services.ingestion.stages import StageRun
from src.services.ingestion.strategies import ChinaReportCommitStrategy
from src.services.exceptions import TaskCancelledError


async def collect(writer, *, crawler, source, force, fill_missing):
    # Index helpers use synchronous requests; keep them off the worker loop.
    from src.services.ingestion.capture import capture_writer
    with capture_writer(writer):
        candidates = await crawler.fetch_list(source=source)
    if not candidates:
        raise RuntimeError(f"China source index returned no reports: {source}")
    candidates = crawler._select_preferred_period_results(candidates)
    selected = candidates if force else (await crawler.check_new_data(candidates, fill_missing=fill_missing))["new"]
    failed = 0
    blocked = 0
    reports = []
    for result in selected:
        try:
            if await task_manager.is_cancel_requested(writer.task_uuid):
                raise TaskCancelledError("Cancellation requested by user")
            if result.content:
                payload, encoding = result.content.encode("utf-8"), "utf-8"
            elif result.url:
                response = await asyncio.to_thread(crawler.get, result.url)
                payload, encoding = response.content, response.encoding or "utf-8"
            else:
                raise ValueError("Report has no URL or content")
            metadata = result.to_dict()
            metadata.pop("content", None)
            metadata.pop("raw_data", None)
            metadata["encoding"] = encoding
        except TaskCancelledError:
            raise
        except Exception:
            failed += 1
            continue
        if HTMLTableParser.is_security_verification(payload.decode(encoding, errors="replace")):
            failed += 1
            blocked += 1
            await writer.write(
                payload, source_url=result.url, kind="blocked_html_report",
                metadata={**metadata, "access_status": "blocked", "reason": "Security Verification challenge"},
            )
            continue
        reports.append(await writer.write(payload, source_url=result.url, kind="html_report", metadata=metadata))
    if selected and not reports:
        if blocked:
            raise RuntimeError(f"China source access blocked by Security Verification ({blocked} reports); response artifacts retained")
        raise RuntimeError("No China report could be collected")
    return reports, failed


async def organize(artifacts, *, processor):
    reports, failed = [], 0
    for artifact in artifacts:
        try:
            metadata = dict(artifact.metadata_)
            encoding = metadata.pop("encoding", "utf-8")
            metadata["date"] = datetime.fromisoformat(metadata["date"]) if metadata.get("date") else None
            payload = await asyncio.to_thread(read_artifact, artifact)
            result = CrawlerResult(content=payload.decode(encoding), **metadata)
            frame = await processor.organize_report(result)
            if frame is None:
                failed += 1
            else:
                reports.append((result, frame))
        except Exception:
            failed += 1
    if artifacts and not reports:
        raise RuntimeError("China processing produced no usable datasets")
    return reports, failed


async def run(service, *, task, source, force, process, fill_missing):
    from src.services.crawl_service import CrawlResult
    stages = await StageRun.start(task.task_uuid, "surveillance")
    writer = ArtifactWriter(task.task_uuid, stages.attempt, "cn")
    crawler, processor = ChinaCDCCrawler(), DataProcessor(country_code="CN")
    async with get_db() as db:
        audit = CrawlRun(country_code="CN", source=source, status="running", started_at=datetime.now(timezone.utc), raw_dir=writer.directory.as_posix(), metadata_={"force": force, "process": process})
        db.add(audit)
        await db.flush()
        run_id = audit.id
    try:
        await stages.transition("collect")
        artifacts, failed = await collect(writer, crawler=crawler, source=source, force=force, fill_missing=fill_missing)
        await stages.finish(metrics={"artifact_count": len(artifacts), "failed_reports": failed,
                                     "blocked_reports": sum(a.kind == "blocked_html_report" for a in writer.artifacts)})
        await stages.transition("organize")
        reports, failed = await organize(artifacts, processor=processor)
        await stages.finish(metrics={"reports": len(reports), "failed_reports": failed})
        await stages.transition("store")
        stored, records, failed = (await ChinaReportCommitStrategy().commit_reports(reports, processor=processor, task_uuid=task.task_uuid)) if process else (0, 0, 0)
        await stages.finish(metrics={"reports": stored, "rows": records, "failed_reports": failed})
        await service._finish_crawl_run(run_id, new_reports=len(artifacts), processed=stored, records=records)
        await task_manager.update_task_progress(task.task_uuid, 100)
        return CrawlResult(len(artifacts), stored, records, run_id)
    except BaseException as exc:
        await stages.finish(error=str(exc) or type(exc).__name__)
        raise
    finally:
        crawler.session.close()
