"""Shared orchestration for configured jurisdiction monthly crawl pipelines.

The country-specific source semantics remain explicit in ``MonthlyPipelineConfig``;
this module only owns the repeated fetch, dual-write, and progress lifecycle.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional


@dataclass(frozen=True)
class MonthlyPipelineConfig:
    country_code: str
    raw_dir_name: str
    start_year: int
    recent_months: int
    default_months_label: str
    fetch_description: str
    mode_recent: str
    upsert_description: str
    finalizing_description: str
    process_disabled_summary: str
    imported_summary: Callable[[int, str], str]
    no_rows_summary: str
    force_fetches_history: bool = False
    supports_fill_missing: bool = True
    refresh_in_thread: bool = False


CONFIGS = {
    "AU": MonthlyPipelineConfig(
        country_code="AU",
        raw_dir_name="au",
        start_year=2000,
        recent_months=3,
        default_months_label="3",
        fetch_description=(
            "Launching Playwright to capture Power BI token, then fetching NINDSS data..."
        ),
        mode_recent="recent 3 months",
        upsert_description="AU data is dynamically revised",
        finalizing_description="Finalizing AU monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed AU source only.",
        imported_summary=lambda count, months: (
            f"AU data upserted: {count} rows across {months} month(s) updated in database."
        ),
        no_rows_summary=(
            "AU source refreshed; no rows matched disease mapping (check mapping config)."
        ),
    ),
    "NZ": MonthlyPipelineConfig(
        country_code="NZ",
        raw_dir_name="nz",
        start_year=2016,
        recent_months=3,
        default_months_label="3",
        fetch_description=(
            "Scraping PHF Science Digital Library for monthly notifiable disease reports..."
        ),
        mode_recent="recent 3 months",
        upsert_description="NZ data is provisional",
        finalizing_description="Finalizing NZ monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed NZ source only.",
        imported_summary=lambda count, months: (
            f"NZ data upserted: {count} rows across {months} month(s) updated in database."
        ),
        no_rows_summary=(
            "NZ source refreshed; no rows matched disease mapping (check mapping config)."
        ),
        force_fetches_history=True,
    ),
    "TW": MonthlyPipelineConfig(
        country_code="TW",
        raw_dir_name="tw",
        start_year=1998,
        recent_months=3,
        default_months_label="3",
        fetch_description="Fetching Taiwan, China CDC NIDSS open-data monthly CSVs...",
        mode_recent="recent 3 months",
        upsert_description="Taiwan, China NIDSS data may be revised",
        finalizing_description=(
            "Finalizing Taiwan, China monthly update and crawl run summary..."
        ),
        process_disabled_summary=(
            "Process disabled, refreshed Taiwan, China source only."
        ),
        imported_summary=lambda count, months: (
            f"Taiwan, China NIDSS data upserted: {count} rows across {months} month(s)."
        ),
        no_rows_summary=(
            "Taiwan, China source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
    ),
    "HK": MonthlyPipelineConfig(
        country_code="HK",
        raw_dir_name="hk",
        start_year=1997,
        recent_months=6,
        default_months_label="latest available 3",
        fetch_description="Fetching Hong Kong CHP annual notifiable disease CSVs...",
        mode_recent="recent available months",
        upsert_description="Hong Kong CHP data may be revised",
        finalizing_description="Finalizing Hong Kong monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed Hong Kong source only.",
        imported_summary=lambda count, months: (
            f"Hong Kong CHP data upserted: {count} rows across {months} month(s)."
        ),
        no_rows_summary=(
            "Hong Kong source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
    ),
    "CA-ON": MonthlyPipelineConfig(
        country_code="CA-ON",
        raw_dir_name="ca/on_idto",
        start_year=2026,
        recent_months=12,
        default_months_label="current-year snapshot",
        fetch_description=(
            "Fetching Public Health Ontario IDTO monthly preliminary data..."
        ),
        mode_recent="complete current-year snapshot",
        upsert_description="Ontario IDTO current-year data are preliminary and revisable",
        finalizing_description=(
            "Finalizing Ontario monthly update and crawl run summary..."
        ),
        process_disabled_summary="Process disabled, refreshed Ontario source only.",
        imported_summary=lambda count, months: (
            f"Ontario IDTO data upserted: {count} rows from {months}."
        ),
        no_rows_summary=(
            "Ontario source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
        supports_fill_missing=False,
        refresh_in_thread=True,
    ),
    "FI": MonthlyPipelineConfig(
        country_code="FI",
        raw_dir_name="fi",
        start_year=1995,
        recent_months=3,
        default_months_label="3",
        fetch_description=(
            "Fetching Finland THL Infectious Diseases Register monthly counts..."
        ),
        mode_recent="dynamic recent-month revision window",
        upsert_description="Finland THL register data may be revised",
        finalizing_description="Finalizing Finland monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed Finland THL source only.",
        imported_summary=lambda count, months: (
            f"Finland THL data upserted: {count} rows across {months} month(s)."
        ),
        no_rows_summary=(
            "Finland THL source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
        force_fetches_history=True,
    ),
    "NO": MonthlyPipelineConfig(
        country_code="NO",
        raw_dir_name="no",
        start_year=1977,
        recent_months=3,
        default_months_label="3",
        fetch_description="Fetching Norway FHI MSIS national monthly notifications...",
        mode_recent="dynamic recent-month revision window",
        upsert_description="Norway FHI MSIS data may be revised",
        finalizing_description="Finalizing Norway monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed Norway FHI source only.",
        imported_summary=lambda count, months: (
            f"Norway FHI MSIS data upserted: {count} rows across {months} month(s)."
        ),
        no_rows_summary=(
            "Norway FHI source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
        force_fetches_history=True,
    ),
    "SE": MonthlyPipelineConfig(
        country_code="SE",
        raw_dir_name="se",
        start_year=2016,
        recent_months=3,
        default_months_label="3",
        fetch_description=(
            "Fetching Sweden Public Health Agency SmiNet national monthly counts..."
        ),
        mode_recent="dynamic recent-month revision window with source-evidence gate",
        upsert_description="Sweden SmiNet data may be revised",
        finalizing_description="Finalizing Sweden monthly update and crawl run summary...",
        process_disabled_summary="Process disabled, refreshed Sweden SmiNet source only.",
        imported_summary=lambda count, months: (
            f"Sweden SmiNet data upserted: {count} rows across {months} month(s)."
        ),
        no_rows_summary=(
            "Sweden SmiNet source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        ),
        force_fetches_history=True,
    ),
}


def _recent_months(now: datetime, count: int) -> set[tuple[int, int]]:
    months = set()
    for delta in range(count):
        month = now.month - delta
        year = now.year
        if month <= 0:
            month += 12
            year -= 1
        months.add((year, month))
    return months


async def _months_to_fetch(config, updater, *, fill_missing, force, get_database):
    if not (fill_missing or force):
        return None

    now = datetime.now()
    try:
        recent_months = max(
            1, min(24, int(getattr(updater, "refresh_recent_months", config.recent_months)))
        )
    except (TypeError, ValueError):
        recent_months = config.recent_months
    months = _recent_months(now, recent_months)
    if config.force_fetches_history and force:
        for year in range(config.start_year, now.year + 1):
            last_month = 12 if year < now.year else now.month
            for month in range(1, last_month + 1):
                months.add((year, month))
    elif fill_missing:
        async with get_database() as db:
            existing_months = await updater.get_db_months(db)
        for year in range(config.start_year, now.year + 1):
            last_month = 12 if year < now.year else now.month
            for month in range(1, last_month + 1):
                if (year, month) not in existing_months:
                    months.add((year, month))
    return sorted(months)


async def execute_monthly_pipeline(
    service,
    *,
    config: MonthlyPipelineConfig,
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
    """Run one configured monthly pipeline without changing its public service API."""
    if fill_missing and not config.supports_fill_missing:
        raise ValueError(
            f"{config.country_code} publishes a complete snapshot; fill_missing is "
            "not supported. Run a normal refresh instead."
        )

    run_started = perf_counter()
    raw_dir = Path("data/raw") / config.raw_dir_name
    run_id: Optional[int] = None

    include_current_month = bool(
        getattr(updater, "include_current_month", False)
    )
    revision_window_months = int(
        getattr(updater, "refresh_recent_months", config.recent_months)
    )
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            f"Country: {config.country_code}\n"
            f"Source: {source}\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"Include Current Month: {'Yes (provisional)' if include_current_month else 'No'}\n"
            f"Revision Window: {revision_window_months} month(s)"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    try:
        async with get_database() as db:
            run = crawl_run_type(
                country_code=config.country_code,
                source=source,
                status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force,
                    "process": process,
                    "include_current_month": include_current_month,
                    "revision_window_months": revision_window_months,
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
    except Exception as exc:
        logger.warning(f"Could not create CrawlRun record: {exc}")

    if config.force_fetches_history and force:
        mode = "all history"
    elif fill_missing:
        mode = "fill missing months"
    else:
        mode = config.mode_recent
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title=f"Phase 1/3: Fetching {config.country_code} Source",
        content=(
            f"{config.fetch_description}\nMode: {mode}"
            + (" + force re-fetch" if force else "")
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    months_to_fetch = await _months_to_fetch(
        config,
        updater,
        fill_missing=fill_missing,
        force=force,
        get_database=get_database,
    )
    phase1_started = perf_counter()
    refresh_kwargs = {
        "source": source,
        "run_external": False,
        "force": force,
        "months": months_to_fetch,
        "save_raw": save_raw,
        "raw_dir": raw_dir if save_raw else None,
    }
    options_factory = getattr(updater, "pipeline_refresh_kwargs", None)
    if callable(options_factory):
        extra_options = options_factory(task)
        if extra_options:
            refresh_kwargs.update(extra_options)
    if config.refresh_in_thread:
        fetched = await asyncio.to_thread(updater.refresh_source, **refresh_kwargs)
    else:
        fetched = updater.refresh_source(**refresh_kwargs)
    phase1_elapsed = perf_counter() - phase1_started
    source_latest = (
        fetched.source_latest_date.isoformat()
        if fetched.source_latest_date
        else "none"
    )
    source_rows = len(fetched.rows)
    months_label = (
        str(len(months_to_fetch))
        if months_to_fetch is not None
        else config.default_months_label
    )

    await task_manager.update_task_progress(task.task_uuid, 30)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Months requested: {months_label}\n"
            f"Source rows prepared (jurisdiction monthly): {source_rows}\n"
            f"Source latest month date: {source_latest}\n"
            f"Current normalized CSV: {fetched.source_csv}\n"
            f"Duration: {phase1_elapsed:.1f}s"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title=f"{config.country_code} Pipeline Logs",
            content="\n".join(fetched.script_logs[-10:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title=f"Phase 2/3: Upserting {config.country_code} Data",
        content=(
            f"{config.upsert_description} — upserting all fetched rows "
            f"({source_rows}) into the database unconditionally..."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 50)

    imported = 0
    skipped_unmapped = 0
    imported_new_data = False
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
            f"Upserted rows: {imported}\n"
            f"Skipped unmapped rows: {skipped_unmapped}"
        ),
        content_type="text",
    )
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 3/3: Finalizing",
        content=config.finalizing_description,
        content_type="text",
    )

    if not process:
        summary_message = config.process_disabled_summary
    elif imported_new_data:
        summary_message = config.imported_summary(imported, months_label)
    elif force:
        summary_message = (
            "Force mode completed; no rows were upserted "
            "(possibly all skipped due to mapping)."
        )
    else:
        summary_message = config.no_rows_summary

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
