"""Ireland HPSC weekly PDF archive crawl orchestration."""

from __future__ import annotations


from src.data.crawlers.ie_weekly_archive import (
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_START_YEAR,
)


def archive_start_year(task) -> int:
    raw = (
        (task.input_data or {}).get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
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
    from src.services.ingestion.countries.ie import run_pipeline

    return await run_pipeline(
        service,
        task=task,
        updater=updater,
        source=source,
        force=force,
        process=process,
        fill_missing=fill_missing,
        start_year=archive_start_year(task),
        get_database=get_database,
    )


__all__ = ["archive_start_year", "execute_ie_weekly_archive_pipeline"]
