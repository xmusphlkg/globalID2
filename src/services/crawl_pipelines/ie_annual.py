"""Ireland HPSC annual-history crawl orchestration."""

from __future__ import annotations


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
    from src.services.ingestion.countries.ie import run_pipeline

    return await run_pipeline(
        service,
        task=task,
        updater=updater,
        source=source,
        force=force,
        process=process,
        fill_missing=fill_missing,
        start_year=annual_history_start_year(task),
        get_database=get_database,
    )


__all__ = ["annual_history_start_year", "execute_ie_annual_pipeline"]
