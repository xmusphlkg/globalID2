"""Monthly source policies and bounded month planning for staged ingestion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    refresh_in_thread: bool = True


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
        refresh_in_thread=True,
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
    "AT": MonthlyPipelineConfig(
        country_code="AT",
        raw_dir_name="at",
        start_year=2025,
        recent_months=3,
        default_months_label="3",
        fetch_description="Fetching Austria AGES Radar monthly national tables...",
        mode_recent="dynamic recent-month revision window",
        upsert_description="Austria AGES Radar data may be revised",
        finalizing_description="Finalizing Austria AGES Radar monthly update...",
        process_disabled_summary="Process disabled, refreshed Austria AGES source only.",
        imported_summary=lambda count, months: f"Austria AGES data upserted: {count} rows across {months} month(s).",
        no_rows_summary="Austria AGES source refreshed; no rows matched legacy mapping.",
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
