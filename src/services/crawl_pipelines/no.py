"""Norway FHI MSIS monthly crawl orchestration.

This module is country-local on purpose: unlike several older monthly sources,
FHI fills the open year with future zero placeholders.  Month planning therefore
uses the updater's closed-month boundary for ordinary, force, and fill-missing
runs alike.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from src.data.crawlers.no import DEFAULT_HISTORY_START_YEAR


def history_start_year(task, updater) -> int:
    """Resolve a caller override without going before FHI's 1977 boundary."""

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
    return max(DEFAULT_HISTORY_START_YEAR, min(selected, datetime.now().year))


async def months_to_fetch(
    updater,
    *,
    start_year: int,
    force: bool,
    fill_missing: bool,
    get_database,
) -> Optional[List[Tuple[int, int]]]:
    """Plan a full/repair run while always refreshing the revision window."""

    if force:
        return updater.history_months(start_year=start_year)
    if not fill_missing:
        return None

    recent = set(updater._default_recent_months())
    async with get_database() as db:
        existing = await updater.get_db_months(db)
    for month in updater.history_months(start_year=start_year):
        if month not in existing:
            recent.add(month)
    return sorted(recent)


async def execute_no_pipeline(
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
    """Keep Norway's independent closed-month planner and three-stage runner."""
    from src.services.ingestion.countries import no
    from src.services.ingestion.runner import run_surveillance

    start_year = history_start_year(task, updater)
    planned = await months_to_fetch(
        updater,
        start_year=start_year,
        force=force,
        fill_missing=fill_missing,
        get_database=get_database,
    )

    # Connector options deliberately include Norway's updater for its local
    # calendar policy; this never passes through configured-monthly dispatch.
    class Connector:
        @staticmethod
        async def collect(writer, *, source, **options):
            return await no.collect(writer, source=source, updater=updater, **options)

        @staticmethod
        def organize(artifacts, *, updater, source, **options):
            return no.organize(artifacts, updater=updater, source=source, **options)

    return await run_surveillance(
        service,
        task=task,
        updater=updater,
        connector=Connector,
        source=source,
        force=force,
        process=process,
        connector_options={"months": planned, "force": force},
    )


__all__ = ["execute_no_pipeline", "history_start_year", "months_to_fetch"]
