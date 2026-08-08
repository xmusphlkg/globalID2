"""Norway FHI MSIS monthly crawl orchestration.

This module is country-local on purpose: unlike several older monthly sources,
FHI fills the open year with future zero placeholders.  Month planning therefore
uses the updater's closed-month boundary for ordinary, force, and fill-missing
runs alike.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import List, Optional, Tuple

from src.data.crawlers.no import DEFAULT_HISTORY_START_YEAR


def history_start_year(task, updater) -> int:
    """Resolve a caller override without going before FHI's 1977 boundary."""

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
    return max(DEFAULT_HISTORY_START_YEAR, min(selected, datetime.now().year))


async def months_to_fetch(
    updater,
    *,
    start_year: int,
    force: bool,
    fill_missing: bool,
    get_database,
) -> Optional[List[Tuple[int, int]]]:
    """Plan a full/repair run while always refreshing the revision window."""

    if force:
        return updater.history_months(start_year=start_year)
    if not fill_missing:
        return None

    recent = set(updater._default_recent_months())
    async with get_database() as db:
        existing = await updater.get_db_months(db)
    for month in updater.history_months(start_year=start_year):
        if month not in existing:
            recent.add(month)
    return sorted(recent)


async def execute_no_pipeline(
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
    """Execute the Norway monthly pipeline without touching shared dispatch."""

    run_started = perf_counter()
    raw_dir = Path("data/raw/no")
    run_id: Optional[int] = None
    start_year = history_start_year(task, updater)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            "Country: NO\n"
            f"Source: {source}\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"History Start Year: {start_year}\n"
            f"Include Open Current Month: "
            f"{'Yes (provisional)' if updater.include_current_month else 'No'}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid,
        save_raw=save_raw,
        raw_dir=raw_dir,
    )

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code="NO",
                source=source,
                status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force,
                    "process": process,
                    "open_month_policy": (
                        "include_provisional"
                        if updater.include_current_month
                        else "closed_months_only"
                    ),
                    "revision_window_months": updater.refresh_recent_months,
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create CrawlRun record: {exc}")

    if force:
        mode = f"full history refresh from {start_year}"
    elif fill_missing:
        mode = f"fill missing months from {start_year} plus recent revisions"
    else:
        mode = (
            f"latest {updater.refresh_recent_months} months"
            + (" including provisional current month" if updater.include_current_month else "")
        )
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching NO Source",
        content=(
            "Fetching FHI MSIS national monthly diagnosis counts with contract "
            f"validation...\nMode: {mode}"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    planned_months = await months_to_fetch(
        updater,
        start_year=start_year,
        force=force,
        fill_missing=fill_missing,
        get_database=get_database,
    )
    phase1_started = perf_counter()
    fetched = updater.refresh_source(
        source=source,
        run_external=False,
        force=force,
        months=planned_months,
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
    requested_count = (
        len(planned_months)
        if planned_months is not None
        else updater.refresh_recent_months
    )

    await task_manager.update_task_progress(task.task_uuid, 30)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Months requested: {requested_count}\n"
            f"Source rows prepared (Norway national monthly): {source_rows}\n"
            f"Latest eligible month: {source_latest}\n"
            f"Current national CSV: {fetched.source_csv}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="NO Pipeline Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting NO Data",
        content=(
            "FHI MSIS counts may be revised; upserting every mapped row from "
            f"the requested window ({source_rows} source rows)..."
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
            result = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date,
                force=force,
            )
            imported = result.inserted_or_updated
            skipped_unmapped = result.skipped_unmapped
            imported_new_data = result.imported_new_data
        await db.commit()

    await task_manager.update_task_progress(task.task_uuid, 80)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"DB latest date: {db_latest_text}\n"
            f"Source latest date: {source_latest}\n"
            f"Upserted rows: {imported}\n"
            f"Skipped unmapped rows: {skipped_unmapped}"
        ),
        content_type="text",
    )

    if not process:
        summary = "Process disabled; refreshed and validated the Norway source only."
    elif imported_new_data:
        summary = (
            f"Norway FHI MSIS data upserted: {imported} rows across "
            f"{requested_count} month(s)."
        )
    elif force:
        summary = "Full Norway history refresh completed; no mapped rows were upserted."
    else:
        summary = "Norway source refreshed; no rows matched the disease mapping."

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


__all__ = ["execute_no_pipeline", "history_start_year", "months_to_fetch"]
