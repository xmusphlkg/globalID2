"""Singapore weekly notification crawl orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from src.generation.site_data_database import ensure_standard_country_rows


async def execute_sg_pipeline(service, *, task, source, force, process, save_raw,
                              fill_missing, updater, get_database, task_manager,
                              crawl_run_type, result_type, logger):
    started, raw_dir, run_id = perf_counter(), Path("data/raw/sg"), None
    raw_start = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
    try:
        start_year = max(updater.full_history_start_year, min(datetime.now().year, int(raw_start or updater.full_history_start_year)))
    except (TypeError, ValueError):
        start_year = updater.full_history_start_year
    mode = "full CSV/PDF/XLSX history" if force else ("history gap fill plus revision window" if fill_missing else f"latest {updater.refresh_recent_weeks} weeks")
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="info", title="Crawl Configuration",
        content=(f"Country: SG\nSource: {source}\nMode: {mode}\nForce: {force}\n"
                 f"Save Raw: {save_raw}\nHistory Start: {start_year}\n"
                 "Sources: 2012-2022 CSV; 2023+ CDA annual workbooks; 2023 PDFs as fallback\n"
                 "Public Release: Enabled — explicit operator authorization; CDA terms status retained"),
        content_type="text",
    )
    await service._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)
    try:
        async with get_database() as db:
            await ensure_standard_country_rows(db, ["SG"])
            run = crawl_run_type(
                country_code="SG", source=source, status="running",
                started_at=datetime.now(timezone.utc), raw_dir=str(raw_dir) if save_raw else None,
                metadata_={"force": force, "fill_missing": fill_missing,
                           "public_release_enabled": True,
                           "license_review_status": "operator_authorized_public_release",
                           "source_terms_status": "cda_written_permission_required"},
            )
            db.add(run); await db.flush(); run_id = run.id
    except Exception as exc:
        logger.warning("Could not create SG CrawlRun record: {}", exc)
    await task_manager.update_task_progress(task.task_uuid, 15)
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="info", title="Phase 1/3: Fetching SG Source",
        content=f"Refreshing Singapore weekly case notifications ({mode})...", content_type="text",
    )
    fetched = updater.refresh_source(
        source=source, force=force, fill_missing=fill_missing, start_year=start_year,
        save_raw=save_raw, raw_dir=raw_dir if save_raw else None,
    )
    await task_manager.update_task_progress(task.task_uuid, 45)
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="success", title="Phase 1/3 Complete",
        content=(f"Rows prepared: {len(fetched.rows)}\nLatest week start: {fetched.source_latest_date or 'none'}\n"
                 f"Current CSV: {fetched.source_csv}\n" + "\n".join(fetched.script_logs)),
        content_type="text",
    )
    imported = skipped = 0; imported_new = False
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        if process:
            outcome = await service._import_rows_with_series(
                db, updater, fetched.rows, db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date, force=force,
            )
            imported, skipped, imported_new = outcome.inserted_or_updated, outcome.skipped_unmapped, outcome.imported_new_data
        await db.commit()
    await task_manager.update_task_progress(task.task_uuid, 85)
    await service._finish_crawl_run(run_id, new_reports=imported, processed=1 if process else 0, records=imported)
    summary = "Source refreshed without database processing." if not process else (f"Singapore source-series observations upserted: {imported}." if imported_new else "Singapore sources refreshed; no observations were upserted.")
    await task_manager.add_workbook_entry(
        task.task_uuid, entry_type="success", title="Crawl Completed",
        content=f"{summary}\nSkipped legacy-unmapped: {skipped}\nDuration: {perf_counter() - started:.1f}s",
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["execute_sg_pipeline"]
