"""Germany RKI SurvStat weekly crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


async def execute_de_pipeline(service, *, task, source, force, process, save_raw, fill_missing, updater, get_database, task_manager, crawl_run_type, result_type, logger):
    """Run full-history/fill-missing/revision-window weekly RKI imports."""
    started, raw_dir, run_id = perf_counter(), Path("data/raw/de"), None
    today = datetime.now(timezone.utc).date()
    raw_start_year = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
    try:
        start_year = max(updater.full_history_start_year, min(today.year, int(raw_start_year or updater.full_history_start_year)))
    except (TypeError, ValueError):
        start_year = updater.full_history_start_year
    if force:
        weeks, mode = updater.history_weeks(today=today, start_year=start_year), f"full RKI history from {start_year}"
    elif fill_missing:
        async with get_database() as db:
            existing = await updater.get_db_week_dates(db)
        weeks = sorted(set(updater.history_weeks(today=today, start_year=start_year)).difference(existing) | set(updater._recent_weeks(today)))
        mode = f"fill missing history plus {updater.refresh_recent_weeks}-week revision window"
    else:
        weeks, mode = updater._recent_weeks(today), f"latest {updater.refresh_recent_weeks} closed weeks"
    await task_manager.add_workbook_entry(task.task_uuid, entry_type="info", title="Crawl Configuration", content=f"Country: DE\nSource: {source}\nMode: {mode}\nForce: {force}\nSave Raw: {save_raw}\nTarget weeks: {len(weeks)}", content_type="text")
    await service._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)
    try:
        async with get_database() as db:
            run = crawl_run_type(country_code="DE", source=source, status="running", started_at=datetime.now(timezone.utc), raw_dir=str(raw_dir) if save_raw else None, metadata_={"force": force, "fill_missing": fill_missing, "target_weeks": len(weeks)})
            db.add(run); await db.flush(); run_id = run.id
    except Exception as exc:
        logger.warning("Could not create DE CrawlRun record: {}", exc)
    await task_manager.add_workbook_entry(task.task_uuid, entry_type="info", title="Phase 1/3: Fetching DE Source", content=f"Exporting national weekly RKI SurvStat CSV/ZIP sessions ({mode})...", content_type="text")
    await task_manager.update_task_progress(task.task_uuid, 15)
    fetched = updater.refresh_source(source=source, force=force, weeks=weeks, save_raw=save_raw, raw_dir=raw_dir if save_raw else None)
    await task_manager.update_task_progress(task.task_uuid, 40)
    await task_manager.add_workbook_entry(task.task_uuid, entry_type="success", title="Phase 1/3 Complete", content=f"Source rows prepared: {len(fetched.rows)}\nSource latest date: {fetched.source_latest_date or 'none'}\nCurrent CSV: {fetched.source_csv}", content_type="text")
    if fetched.script_logs:
        await task_manager.add_workbook_entry(task.task_uuid, entry_type="info", title="DE Pipeline Logs", content="\n".join(fetched.script_logs), content_type="text")
    imported = skipped = 0; imported_new = False
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        if process:
            outcome = await service._import_rows_with_series(db, updater, fetched.rows, db_latest_date=db_latest, source_latest_date=fetched.source_latest_date, force=force)
            imported, skipped, imported_new = outcome.inserted_or_updated, outcome.skipped_unmapped, outcome.imported_new_data
        await db.commit()
    await task_manager.update_task_progress(task.task_uuid, 80)
    await task_manager.add_workbook_entry(task.task_uuid, entry_type="success", title="Phase 2/3 Complete", content=f"Upserted rows: {imported}\nSkipped legacy-unmapped rows: {skipped}\nSource-series rows remain losslessly stored by source category.", content_type="text")
    await service._finish_crawl_run(run_id, new_reports=imported, processed=1 if process else 0, records=imported)
    summary = "Process disabled; RKI source was refreshed only." if not process else (f"Germany RKI source-native weekly observations upserted: {imported}." if imported_new else "Germany RKI source refreshed; no source-native observations were upserted.")
    await task_manager.add_workbook_entry(task.task_uuid, entry_type="success", title="Crawl Completed", content=f"Summary: {summary}\nDuration: {perf_counter() - started:.1f}s", content_type="text")
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)
