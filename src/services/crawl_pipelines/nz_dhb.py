"""Annual New Zealand notifiable-disease DHB subdivision ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from src.generation.site_data_database import ensure_standard_country_rows


async def execute_nz_dhb_pipeline(
    service,
    *,
    task,
    source,
    force,
    process,
    save_raw,
    fill_missing,
    updater,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    logger,
):
    """Import annual DHB facts while keeping them out of national monthly rows."""
    started = perf_counter()
    country_code = str(updater.country_code).upper()
    raw_dir = Path("data/raw/nz/dhb") / country_code.casefold()
    run_id = None
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            f"Jurisdiction: {country_code}\nSource: {source}\n"
            "Frequency: annual PHF Science DHB notifiable-disease counts\n"
            f"Force: {force}\nSave Raw: {save_raw}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    async with get_database() as db:
        await ensure_standard_country_rows(db, [country_code])
        run = crawl_run_type(
            country_code=country_code,
            source=source,
            status="running",
            started_at=datetime.now(timezone.utc),
            raw_dir=str(raw_dir) if save_raw else None,
            metadata_={
                "force": force,
                "process": process,
                "frequency": "annual",
                "location_type": "subdivision",
                "source_id": "SRC_NZ_PHS_DHB",
                "public_release_enabled": True,
                "license": "CC BY 4.0",
            },
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

    await task_manager.update_task_progress(task.task_uuid, 15)
    fetched = await asyncio.to_thread(
        updater.refresh_source,
        source=source,
        force=force,
        save_raw=save_raw,
        raw_dir=raw_dir if save_raw else None,
    )
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Annual DHB Fetch Complete",
        content=(
            f"Rows prepared: {len(fetched.series_rows)}\n"
            f"Latest year: {fetched.source_latest_date or 'none'}\n"
            f"Source file: {fetched.source_csv}\n"
            + "\n".join(fetched.script_logs)
        ),
        content_type="text",
    )

    imported = 0
    skipped = 0
    imported_new_data = False
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        if process:
            outcome = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                series_rows=fetched.series_rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date,
                force=force,
            )
            imported = outcome.inserted_or_updated
            skipped = outcome.skipped_unmapped
            imported_new_data = outcome.imported_new_data
        await db.commit()

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
            f"Annual source-series observations upserted: {imported}\n"
            f"Unmapped legacy rows: {skipped}\nNew data: {imported_new_data}\n"
            f"Duration: {perf_counter() - started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["execute_nz_dhb_pipeline"]
