"""ECDC annual baseline crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from src.generation.site_data_database import ensure_standard_country_rows


async def execute_ecdc_pipeline(service, *, task, source, force, process, save_raw,
                                fill_missing, updater, get_database, task_manager,
                                crawl_run_type, result_type, logger):
    started = perf_counter()
    country = updater.country_code
    raw_dir = Path(f"data/raw/{country.casefold()}")
    raw_start = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
    try:
        start_year = max(updater.full_history_start_year, int(raw_start or updater.full_history_start_year))
    except (TypeError, ValueError):
        start_year = updater.full_history_start_year
    run_id = None
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="info", title="Crawl Configuration",
        content=(f"Country: {country}\nSource: {source}\nMode: ECDC annual baseline from {start_year}\n"
                 f"Force: {force}\nSave Raw: {save_raw}\n"
                 "Metric: Member-State reported cases, country/annual resolution\n"
                 "Missing policy: absent cells unknown; published zeroes preserved\n"
                 "Reuse: publication/reproduction authorized with ECDC and Member State attribution"),
        content_type="text",
    )
    await service._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)
    try:
        async with get_database() as db:
            await ensure_standard_country_rows(db, [country])
            run = crawl_run_type(
                country_code=country, source=source, status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force, "fill_missing": fill_missing,
                    "start_year": start_year, "public_release_enabled": True,
                    "license_review_status": "ecdc_publication_and_reproduction_authorized_with_attribution",
                },
            )
            db.add(run); await db.flush(); run_id = run.id
    except Exception as exc:
        logger.warning("Could not create ECDC CrawlRun record: {}", exc)
    await task_manager.update_task_progress(task.task_uuid, 15)
    fetched = updater.refresh_source(
        source=source, force=force, fill_missing=fill_missing,
        start_year=start_year, save_raw=save_raw,
        raw_dir=raw_dir if save_raw else None,
    )
    await task_manager.update_task_progress(task.task_uuid, 55)
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="success", title="ECDC Fetch Complete",
        content=(f"Rows prepared: {len(fetched.rows)}\nLatest year: {fetched.source_latest_date or 'none'}\n"
                 f"Current CSV: {fetched.source_csv}\n" + "\n".join(fetched.script_logs)),
        content_type="text",
    )
    imported = skipped = deleted = 0; imported_new = False
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        if process:
            deleted = await updater.delete_authoritative_window(
                db, start_year=start_year
            )
            outcome = await service._import_rows_with_series(
                db, updater, fetched.rows, db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date, force=force,
            )
            imported = outcome.inserted_or_updated
            skipped = outcome.skipped_unmapped
            imported_new = outcome.imported_new_data
        await db.commit()
    await task_manager.update_task_progress(task.task_uuid, 90)
    await service._finish_crawl_run(run_id, new_reports=imported, processed=1 if process else 0, records=imported)
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="success", title="Crawl Completed",
        content=(f"Source-series observations upserted: {imported}\n"
                 f"Authoritative-window observations replaced: {deleted}\n"
                 f"Skipped legacy-unmapped: {skipped}\nNew data: {imported_new}\n"
                 f"Duration: {perf_counter() - started:.1f}s"),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["execute_ecdc_pipeline"]
