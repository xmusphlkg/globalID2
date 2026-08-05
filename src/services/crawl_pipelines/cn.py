"""China CDC report crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional


async def execute_cn_pipeline(
    service,
    *,
    task,
    country_code: str,
    source: str,
    force: bool,
    process: bool,
    save_raw: bool,
    fill_missing: bool,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    crawler_type,
    processor_type,
    logger,
):
    """Execute the China CDC crawl, parse, and store pipeline."""
    crawler = crawler_type()
    raw_dir = Path("data/raw") / country_code.lower()
    run_started = perf_counter()

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            f"Country: {country_code}\n"
            f"Source: {source}\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    run_id: Optional[int] = None
    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code=country_code,
                source=source,
                status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={"force": force, "process": process},
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create CrawlRun record: {exc}")

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching Data List",
        content="Fetching available data list...",
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    phase1_started = perf_counter()
    results = await crawler.crawl(
        source=source, force=force, fill_missing=fill_missing
    )
    phase1_elapsed = perf_counter() - phase1_started
    await task_manager.update_task_progress(task.task_uuid, 30)

    crawl_stats = getattr(crawler, "last_crawl_stats", {}) or {}
    max_date = crawl_stats.get("max_date") or "none"
    missing_months = crawl_stats.get("missing_months") or []
    missing_count = int(crawl_stats.get("missing_months_count") or 0)
    missing_preview = ", ".join(missing_months[:8]) if missing_months else "none"
    if missing_count > 8:
        missing_preview = f"{missing_preview} ... (+{missing_count - 8})"

    source_counts = service._source_distribution(results)
    source_summary = ", ".join(
        f"{key}: {value}" for key, value in source_counts.items()
    ) or "none"
    total_candidates = int(crawl_stats.get("total_candidates") or 0)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Source filter: {source}\n"
            f"Source candidates found: {total_candidates}\n"
            f"Reports selected for processing: {len(results)}\n"
            f"Selected distribution: {source_summary}\n"
            f"DB latest date: {max_date}\n"
            f"Missing months in DB: {missing_count}\n"
            f"Missing month sample: {missing_preview}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )

    if not results:
        await service._finish_crawl_run(
            run_id, new_reports=0, processed=0, records=0
        )
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Completed",
            content=(
                "No new data found after incremental check.\n"
                f"Source filter: {source}\n"
                f"DB latest date: {max_date}\n"
                f"Missing months in DB: {missing_count}\n"
                f"Force mode: {'Yes' if force else 'No'}\n"
                f"Fill missing months: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)
        return result_type(0, 0, 0, run_id)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Data List Retrieved",
        content=f"Found {len(results)} {'new ' if not force else ''}report(s).",
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 40)

    total_records = 0
    processed_dfs = []

    if process and results:
        phase2_started = perf_counter()
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Processing Data",
            content=f"Processing {len(results)} report(s)...",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        processor = processor_type(
            output_dir=Path("data/processed") / country_code.lower(),
            country_code=country_code.lower(),
        )

        async def _progress(current, total, message):
            pct = 50 + int((current / total) * 30) if total else 50
            await task_manager.update_task_progress(task.task_uuid, pct)
            if current % 10 == 0 or current == total:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="info",
                    title="Processing Progress",
                    content=(
                        f"{current}/{total} reports processed"
                        + (f"\nLast step: {message}" if message else "")
                    ),
                    content_type="text",
                )

        processed_dfs = await processor.process_crawler_results(
            results,
            save_to_file=True,
            save_raw=save_raw,
            crawl_run_id=run_id,
            raw_dir=raw_dir,
            progress_callback=_progress,
        )
        total_records = sum(len(frame) for frame in processed_dfs)
        phase2_elapsed = perf_counter() - phase2_started

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"Processed datasets: {len(processed_dfs)}/{len(results)}\n"
                f"Records stored: {total_records}\n"
                f"Duration: {phase2_elapsed:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 80)

    elif save_raw and results:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Saving Raw Data",
            content="Saving raw pages...",
            content_type="text",
        )
        processor = processor_type(
            output_dir=Path("data/processed") / country_code.lower(),
            country_code=country_code.lower(),
        )
        await processor.save_raw_pages(
            results, crawl_run_id=run_id, raw_dir=raw_dir
        )
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Raw Data Saved",
            content=f"Saved raw pages for {len(results)} report(s) to {raw_dir}",
            content_type="text",
        )

    processed_count = len(processed_dfs) if process else 0
    await service._finish_crawl_run(
        run_id, len(results), processed_count, total_records
    )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Crawl Completed",
        content=(
            f"New reports: {len(results)}\n"
            f"Processed: {processed_count}\n"
            f"Records: {total_records}\n"
            f"Source distribution: {source_summary}\n"
            f"Total duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)

    return result_type(len(results), processed_count, total_records, run_id)
