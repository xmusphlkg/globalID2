"""Ireland HPSC national weekly crawl orchestration."""

from __future__ import annotations

from datetime import datetime

from src.data.crawlers.ie import DEFAULT_HISTORY_START


def history_start_year(task, updater) -> int:
    """Resolve a caller override without predating the HPSC source boundary."""

    raw_value = (
        (task.input_data or {}).get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
    try:
        selected = (
            int(raw_value)
            if raw_value is not None
            else int(updater.full_history_start_year)
        )
    except (TypeError, ValueError):
        selected = int(updater.full_history_start_year)
    return max(DEFAULT_HISTORY_START[0], min(selected, datetime.now().year))


async def execute_ie_pipeline(
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
        start_year=history_start_year(task, updater),
        get_database=get_database,
    )


__all__ = ["execute_ie_pipeline", "history_start_year"]
