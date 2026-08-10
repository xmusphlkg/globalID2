"""Ireland HPSC weekly PDF archive crawl orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional

from src.data.crawlers.ie_weekly_archive import (
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_START_YEAR,
)


def archive_start_year(task) -> int:
    raw = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
    try:
        selected = int(raw) if raw is not None else DEFAULT_ARCHIVE_START_YEAR
    except (TypeError, ValueError):
        selected = DEFAULT_ARCHIVE_START_YEAR
    return max(DEFAULT_ARCHIVE_START_YEAR, min(selected, DEFAULT_ARCHIVE_END[0]))


async def execute_ie_weekly_archive_pipeline(
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
    run_started = perf_counter()
    raw_dir = Path("data/raw/ie/weekly_archive")
    run_id: Optional[int] = None
    start_year = archive_start_year(task)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="warning",
        title="Crawl Configuration",
        content=(
            "Country: IE\n"
            f"Source: {source}\n"
            "Temporal Grain: weekly historical snapshots\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"Archive Boundary: {start_year}–2021 W29\n"
            "Missing Week Semantics: not archived / unknown; never zero-filled\n"
            "Licence Validation: skipped for ingestion; public release disabled"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code="IE", source=source, status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force,
                    "process": process,
                    "temporal_granularity": "weekly",
                    "history_start_year": start_year,
                    "history_end_period": "2021-W29",
                    "dataset_status": "historical_provisional_snapshot",
                    "public_release_enabled": False,
                    "license_review_status": "not_checked_for_ingestion",
                    "missing_week_semantics": "not_archived_not_zero",
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create IE weekly archive CrawlRun record: {exc}")

    existing_weeks = None
    if fill_missing and not force:
        async with get_database() as db:
            existing_weeks = await updater.get_db_weeks(db)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Rebuilding HPSC Weekly Archive",
        content=(
            "Enumerating Lenus and Internet Archive captures of official HPSC PDFs, "
            "then extracting only Table 1's "
            "current-week national count column."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    phase1_started = perf_counter()
    fetched = updater.refresh_source(
        source=source,
        force=force,
        fill_missing=fill_missing,
        existing_weeks=existing_weeks,
        start_year=start_year,
        save_raw=save_raw,
        raw_dir=raw_dir if save_raw else None,
    )
    phase1_elapsed = perf_counter() - phase1_started
    source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
    await task_manager.update_task_progress(task.task_uuid, 40)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Reports fetched: {len(fetched.periods_fetched)}\n"
            f"Rows prepared: {len(fetched.rows)}\n"
            f"Catalogue reports in supported boundary: {len(fetched.catalogue_periods)}\n"
            f"Unarchived weeks in boundary: {len(fetched.missing_periods)}\n"
            f"Latest archived week Monday: {source_latest}\n"
            f"Current CSV: {fetched.source_csv}\n"
            f"Coverage manifest: {fetched.coverage_path}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="IE Weekly Archive Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting Archive Source Series",
        content=(
            "Upserting immutable provisional PDF snapshots to the independent "
            "archive series. They are not merged into the current NDH source or "
            "the legacy disease fact table."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 55)

    imported = 0
    db_latest_text = "none"
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        db_latest_text = db_latest.isoformat() if db_latest else "none"
        if process and fetched.rows:
            imported_result = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date,
                force=force,
            )
            imported = imported_result.inserted_or_updated
        await db.commit()

    await task_manager.update_task_progress(task.task_uuid, 85)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"DB latest archive week before run: {db_latest_text}\n"
            f"Source latest archive week: {source_latest}\n"
            f"Archive source-series observations upserted: {imported}\n"
            "Legacy weekly rows upserted: 0 (intentional source boundary)"
        ),
        content_type="text",
    )

    await service._finish_crawl_run(
        run_id, new_reports=imported, processed=1 if process else 0, records=imported
    )
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Crawl Completed",
        content=(
            f"Prepared rows: {len(fetched.rows)}\n"
            f"Imported archive observations: {imported}\n"
            f"Explicitly unarchived weeks: {len(fetched.missing_periods)}\n"
            "Licence validation was skipped for ingestion; public release is disabled.\n"
            f"Duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["archive_start_year", "execute_ie_weekly_archive_pipeline"]
