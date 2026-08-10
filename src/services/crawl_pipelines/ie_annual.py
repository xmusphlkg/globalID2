"""Ireland HPSC annual-history crawl orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional

from src.data.crawlers.ie_annual import (
    DEFAULT_ANNUAL_END_YEAR,
    DEFAULT_ANNUAL_START_YEAR,
)


def annual_history_start_year(task) -> int:
    raw = (
        (task.input_data or {}).get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
    try:
        selected = int(raw) if raw is not None else DEFAULT_ANNUAL_START_YEAR
    except (TypeError, ValueError):
        selected = DEFAULT_ANNUAL_START_YEAR
    return max(DEFAULT_ANNUAL_START_YEAR, min(selected, DEFAULT_ANNUAL_END_YEAR))


async def execute_ie_annual_pipeline(
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
    raw_dir = Path("data/raw/ie/annual")
    run_id: Optional[int] = None
    start_year = annual_history_start_year(task)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="warning",
        title="Crawl Configuration",
        content=(
            "Country: IE\n"
            f"Source: {source}\n"
            "Temporal Grain: annual\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"History Range: {start_year}–{DEFAULT_ANNUAL_END_YEAR}\n"
            "Boundary: annual history ends at 2020; weekly NDH starts in 2021\n"
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
                    "temporal_granularity": "annual",
                    "history_start_year": start_year,
                    "history_end_year": DEFAULT_ANNUAL_END_YEAR,
                    "public_release_enabled": False,
                    "license_review_status": "written_permission_required",
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create IE annual CrawlRun record: {exc}")

    existing_years = None
    if fill_missing and not force:
        async with get_database() as db:
            existing_years = await updater.get_db_years(db)

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching IE Annual History",
        content=(
            "Fetching reviewed HPSC consolidated annual PDF tables. "
            "NA cells remain missing/not-applicable and are never converted to zero."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    phase1_started = perf_counter()
    fetched = updater.refresh_source(
        source=source,
        force=force,
        fill_missing=fill_missing,
        existing_years=existing_years,
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
    await task_manager.update_task_progress(task.task_uuid, 35)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Years fetched: {', '.join(map(str, fetched.years_fetched))}\n"
            f"Annual source rows prepared: {len(fetched.rows)}\n"
            f"Latest annual period: {source_latest}\n"
            f"Current annual CSV: {fetched.source_csv}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="IE Annual Pipeline Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting IE Annual Source Series",
        content=(
            "Upserting the annual source grain into the source-series store. "
            "It is deliberately not projected into the legacy weekly fact table."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 50)

    imported = 0
    db_latest_text = "none"
    async with get_database() as db:
        db_latest = await updater.get_db_latest_date(db)
        db_latest_text = db_latest.isoformat() if db_latest else "none"
        if process:
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

    await task_manager.update_task_progress(task.task_uuid, 80)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"DB latest annual period before run: {db_latest_text}\n"
            f"Source latest annual period: {source_latest}\n"
            f"Non-missing source-series observations upserted: {imported}\n"
            "Legacy weekly rows upserted: 0 (intentional grain boundary)"
        ),
        content_type="text",
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
            f"Prepared rows: {len(fetched.rows)}\n"
            f"Imported annual observations: {imported}\n"
            "Public release remains permission-gated.\n"
            f"Duration: {perf_counter() - run_started:.1f}s"
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    return result_type(imported, 1 if process else 0, imported, run_id)


__all__ = ["annual_history_start_year", "execute_ie_annual_pipeline"]
