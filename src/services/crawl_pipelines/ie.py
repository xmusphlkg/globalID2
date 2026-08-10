"""Ireland HPSC national weekly crawl orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional

from src.data.crawlers.ie import DEFAULT_HISTORY_START


def history_start_year(task, updater) -> int:
    """Resolve a caller override without predating the HPSC source boundary."""

    raw_value = (
        (task.input_data or {}).get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
    try:
        selected = (
            int(raw_value)
            if raw_value is not None
            else int(updater.full_history_start_year)
        )
    except (TypeError, ValueError):
        selected = int(updater.full_history_start_year)
    return max(DEFAULT_HISTORY_START[0], min(selected, datetime.now().year))


async def execute_ie_pipeline(
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
    """Execute the permission-gated HPSC weekly refresh and dual write."""

    run_started = perf_counter()
    raw_dir = Path("data/raw/ie")
    run_id: Optional[int] = None
    start_year = history_start_year(task, updater)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="warning",
        title="Crawl Configuration",
        content=(
            "Country: IE\n"
            f"Source: {source}\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"History Start Year: {start_year}\n"
            f"Revision Window: {updater.refresh_recent_weeks} weeks\n"
            "Public Release: Disabled — HPSC written permission required"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code="IE",
                source=source,
                status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force,
                    "process": process,
                    "revision_window_weeks": updater.refresh_recent_weeks,
                    "public_release_enabled": False,
                    "license_review_status": "written_permission_required",
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create IE CrawlRun record: {exc}")

    existing_weeks = None
    if fill_missing and not force:
        async with get_database() as db:
            existing_weeks = await updater.get_db_weeks(db)

    if force:
        mode = f"full source history from {start_year}"
    elif fill_missing:
        mode = "missing source weeks plus latest revision window"
    else:
        mode = f"latest {updater.refresh_recent_weeks} source weeks"

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching IE Source",
        content=(
            "Fetching HPSC ArcGIS national total / Weekly Number of Cases rows "
            "with schema and ISO-week contract validation...\n"
            f"Mode: {mode}"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    phase1_started = perf_counter()
    fetched = updater.refresh_source(
        source=source,
        run_external=False,
        force=force,
        fill_missing=fill_missing,
        existing_weeks=existing_weeks,
        start_year=start_year,
        save_raw=save_raw,
        raw_dir=raw_dir if save_raw else None,
    )
    phase1_elapsed = perf_counter() - phase1_started
    source_latest = (
        fetched.source_latest_date.isoformat()
        if fetched.source_latest_date
        else "none"
    )
    source_rows = len(fetched.rows)

    await task_manager.update_task_progress(task.task_uuid, 35)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Weeks fetched: {len(fetched.periods_fetched)}\n"
            f"Source rows prepared: {source_rows}\n"
            f"Latest source week Monday: {source_latest}\n"
            f"Source modified at: {fetched.source_updated_at or 'unknown'}\n"
            f"Current national CSV: {fetched.source_csv}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="IE Pipeline Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting IE Data",
        content=(
            "Upserting reviewed legacy mappings and every registered HPSC "
            "source series atomically. Public site generation remains disabled."
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
        if process:
            import_result = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date,
                force=force,
            )
            imported = import_result.inserted_or_updated
            skipped_unmapped = import_result.skipped_unmapped
            imported_new_data = import_result.imported_new_data
        await db.commit()

    await task_manager.update_task_progress(task.task_uuid, 80)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"DB latest date: {db_latest_text}\n"
            f"Source latest date: {source_latest}\n"
            f"Legacy rows upserted: {imported}\n"
            f"Legacy rows skipped as unmapped: {skipped_unmapped}"
        ),
        content_type="text",
    )

    if not process:
        summary = "Process disabled; refreshed and validated the HPSC source only."
    elif imported_new_data:
        summary = (
            f"Ireland HPSC internal data upserted: {imported} legacy rows; "
            "public release remains disabled."
        )
    elif force:
        summary = "Full IE source refresh completed; no mapped rows were upserted."
    else:
        summary = "IE source refreshed; no rows matched the reviewed legacy mapping."

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
            f"Summary: {summary}\n"
            f"Prepared rows: {source_rows}\n"
            f"Imported rows: {imported}\n"
            f"Duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["execute_ie_pipeline", "history_start_year"]
