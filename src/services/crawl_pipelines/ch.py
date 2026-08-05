"""Switzerland FOPH IDD monthly crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional


def history_start_year(task, updater) -> int:
    """Resolve and clamp the configured FOPH history boundary."""
    raw_value = (
        (task.input_data or {}).get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
    try:
        start_year = (
            int(raw_value)
            if raw_value is not None
            else int(updater.full_history_start_year)
        )
    except (TypeError, ValueError):
        start_year = int(updater.full_history_start_year)

    current_year = datetime.now().year
    return max(1900, min(start_year, current_year))


async def execute_ch_pipeline(
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
    """Execute the Switzerland FOPH IDD API pipeline."""
    run_started = perf_counter()
    raw_dir = Path("data/raw") / "ch"
    run_id: Optional[int] = None
    start_year = history_start_year(task, updater)

    if force:
        mode_text = f"full IDD history refresh from {start_year}"
    elif fill_missing:
        mode_text = f"fill missing months from {start_year}"
    else:
        mode_text = "recent IDD periods"

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            "Country: CH\n"
            f"Source: {source}\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"History Start Year: {start_year}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code="CH",
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
        title="Phase 1/3: Fetching CH Source",
        content=(
            "Fetching Switzerland FOPH/BAG IDD mandatory reporting API series...\n"
            f"Mode: {mode_text}"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    months_to_fetch = None
    if fill_missing and not force:
        now_dt = datetime.now()
        recent: list[tuple[int, int]] = []
        for delta in range(max(1, updater.refresh_recent_months)):
            month = now_dt.month - delta
            year = now_dt.year
            if month <= 0:
                month += 12
                year -= 1
            recent.append((year, month))
        months_set = set(recent)

        async with get_database() as db:
            existing_months = await updater.get_db_months(db)
        for year in range(start_year, now_dt.year + 1):
            last_month = 12 if year < now_dt.year else now_dt.month
            for month in range(1, last_month + 1):
                if (year, month) not in existing_months:
                    months_set.add((year, month))

        months_to_fetch = sorted(months_set)

    phase1_started = perf_counter()
    fetched = updater.refresh_source(
        source=source,
        run_external=False,
        force=force,
        months=months_to_fetch,
        history=force,
        start_year=start_year if force else None,
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
    periods_requested = (
        str(len(months_to_fetch))
        if months_to_fetch is not None
        else ("all history" if force else "recent configured periods")
    )

    await task_manager.update_task_progress(task.task_uuid, 30)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Periods requested: {periods_requested}\n"
            f"Source rows prepared: {source_rows}\n"
            f"Source latest date: {source_latest}\n"
            f"IDD data version: {fetched.version or 'unknown'}\n"
            f"Current national CSV: {fetched.source_csv}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )

    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="CH Pipeline Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting CH Data",
        content=(
            "Swiss IDD data may be revised — upserting all fetched rows "
            f"({source_rows}) into the database unconditionally..."
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
            f"Upserted rows: {imported}\n"
            f"Skipped unmapped rows: {skipped_unmapped}"
        ),
        content_type="text",
    )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 3/3: Finalizing",
        content="Finalizing Switzerland IDD update and crawl run summary...",
        content_type="text",
    )

    if not process:
        summary_message = "Process disabled, refreshed Switzerland IDD source only."
    elif imported_new_data:
        summary_message = f"Switzerland IDD data upserted: {imported} rows."
    elif force:
        summary_message = (
            "Force mode completed; no rows were upserted "
            "(possibly all skipped due to mapping)."
        )
    else:
        summary_message = (
            "Switzerland source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        )

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
            f"Prepared rows: {source_rows}\n"
            f"Imported rows: {imported}\n"
            f"Duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)

    return result_type(imported, 1 if process else 0, imported, run_id)
