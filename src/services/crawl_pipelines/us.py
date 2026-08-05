"""United States CDC weekly/annual crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional


async def execute_us_pipeline(
    service,
    *,
    task,
    source: str,
    force: bool,
    process: bool,
    save_raw: bool,
    fill_missing: bool,
    updater,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    logger,
):
    """Execute the US CDC source refresh and dual-write pipeline."""
    run_started = perf_counter()
    raw_dir = Path("data/raw") / "us"
    run_id: Optional[int] = None

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            "Country: US\n"
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

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code="US",
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
        content=(
            f"Fetching US CDC source '{source}' "
            "(NNDSS weekly and/or NHSS annual HIV data)..."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    phase1_started = perf_counter()
    fetched = updater.fetch_latest(source=source)
    phase1_elapsed = perf_counter() - phase1_started
    source_latest = fetched.latest_date.isoformat() if fetched.latest_date else "none"
    source_latest_detail = ", ".join(
        f"{name}: {latest.isoformat() if latest else 'none'}"
        for name, latest in fetched.latest_by_source.items()
    ) or "none"
    source_rows = len(fetched.rows)

    await task_manager.update_task_progress(task.task_uuid, 30)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Source rows fetched: {source_rows}\n"
            f"Source latest date: {source_latest}\n"
            f"Latest by source: {source_latest_detail}\n"
            f"Source endpoint: {fetched.source_ref}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Checking Incremental Updates",
        content=(
            "Checking each source independently; NNDSS recent weeks and all "
            "NHSS annual revisions remain eligible for upsert..."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 50)

    imported = 0
    skipped_unmapped = 0
    imported_new_data = False
    db_latest_text = "none"

    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        db_latest_text = db_latest.isoformat() if db_latest else "none"

        if not process:
            imported_new_data = False
            imported = 0
        else:
            import_result = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                series_rows=fetched.series_rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.latest_date,
                force=force,
            )
            imported = import_result.inserted_or_updated
            skipped_unmapped = import_result.skipped_unmapped
            imported_new_data = import_result.imported_new_data

    await task_manager.update_task_progress(task.task_uuid, 80)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"DB latest date: {db_latest_text}\n"
            f"Source latest date: {source_latest}\n"
            f"Imported rows: {imported}\n"
            f"Skipped unmapped rows: {skipped_unmapped}"
        ),
        content_type="text",
    )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 3/3: Finalizing",
        content="Finalizing US surveillance update and crawl run summary...",
        content_type="text",
    )

    if not process:
        summary_message = "Process disabled, fetched source rows only."
    elif imported_new_data:
        summary_message = "US surveillance data imported or revised successfully."
    elif force:
        summary_message = "Force mode completed; no rows were upserted after mapping."
    else:
        summary_message = "No eligible US source rows were detected, import skipped."

    await service._finish_crawl_run(
        run_id,
        new_reports=imported,
        processed=1 if process else 0,
        records=imported,
    )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Crawl Completed",
        content=(
            f"Summary: {summary_message}\n"
            f"Fetched rows: {source_rows}\n"
            f"Imported rows: {imported}\n"
            f"Duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)

    return result_type(imported, 1 if process else 0, imported, run_id)
