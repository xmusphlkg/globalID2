"""HPSC current weekly, annual, and historical weekly raw source connectors.

Collection discovers endpoints and saves responses; case normalization runs only
against the immutable responses in ``organize``. All three sources retain their
independent clocks and their disabled public-release policy.
"""

from __future__ import annotations
import asyncio
import copy
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from src.data.crawlers.ie import (
    IEWeek,
    iter_iso_weeks,
    recent_source_weeks,
    DEFAULT_QUERY_WEEK_BATCH,
)
from src.data.crawlers.ie_annual import (
    DEFAULT_ANNUAL_END_YEAR,
    DEFAULT_ANNUAL_START_YEAR,
)
from src.data.crawlers.ie_weekly_archive import (
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_START_YEAR,
)
from src.data.processors.ie_annual import DEFAULT_REVISION_YEARS
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from src.services.ingestion.replay import offline_crawler_type


def _annual_years(*, start_year, force, fill_missing, existing_years):
    start = max(
        DEFAULT_ANNUAL_START_YEAR,
        min(int(start_year or DEFAULT_ANNUAL_START_YEAR), DEFAULT_ANNUAL_END_YEAR),
    )
    catalogue = set(range(start, DEFAULT_ANNUAL_END_YEAR + 1))
    if force:
        selected = catalogue
    elif fill_missing:
        selected = (
            catalogue - set(existing_years or ())
        ) | DEFAULT_REVISION_YEARS.intersection(catalogue)
    else:
        selected = DEFAULT_REVISION_YEARS.intersection(catalogue)
    return selected or DEFAULT_REVISION_YEARS.intersection(catalogue)


def _weekly_transport(
    crawler, updater, *, force, fill_missing, existing_weeks, start_year
):
    contract = crawler.fetch_service_contract()
    crawler.fetch_disease_catalog()
    earliest, latest = crawler.fetch_source_bounds()
    start = max(int(start_year or updater.full_history_start_year), 2021)
    if force or fill_missing:
        first = IEWeek.from_parts(start, 1) if start > earliest.year else earliest
        periods = iter_iso_weeks(first, latest)
        if not force:
            periods = [
                p for p in periods if (p.year, p.week) not in set(existing_weeks or ())
            ]
            periods = sorted(
                set(periods)
                | set(recent_source_weeks(latest, updater.refresh_recent_weeks))
            )
    else:
        periods = recent_source_weeks(latest, updater.refresh_recent_weeks)
    periods = [
        p
        for p in periods
        if p.year >= start and earliest.monday <= p.monday <= latest.monday
    ]
    if not periods:
        raise ValueError("IE HPSC request contains no source-available weeks")
    size = min(1000, contract.max_record_count)
    for first in range(0, len(periods), DEFAULT_QUERY_WEEK_BATCH):
        batch = periods[first : first + DEFAULT_QUERY_WEEK_BATCH]
        offset = 0
        while True:
            params = [
                ("where", crawler._week_where(batch)),
                (
                    "outFields",
                    "Disease,year,week,YearWeek,location,Cat,Format,SubCat,Value,Unique_ID,Include,ObjectId",
                ),
                ("returnGeometry", "false"),
                ("orderByFields", "year,week,Disease,ObjectId"),
                ("resultOffset", str(offset)),
                ("resultRecordCount", str(size)),
                ("f", "json"),
            ]
            # The page envelope is read only for pagination. Case rows are not
            # validated, transformed, mapped, or materialized in collect.
            payload = crawler.get(crawler.query_url, params=params).json()
            features = payload.get("features") if isinstance(payload, dict) else None
            if not isinstance(features, list):
                raise ValueError("HPSC pagination envelope lacks features")
            if not payload.get("exceededTransferLimit") and len(features) < size:
                break
            if not features:
                raise ValueError("HPSC pagination did not advance")
            offset += len(features)


async def collect(
    writer,
    *,
    updater,
    source,
    force=False,
    fill_missing=False,
    start_year=None,
    existing_weeks=None,
    existing_years=None,
):
    if updater.public_release_enabled:
        raise ValueError("HPSC source public release must remain disabled")
    if updater.output_csv.exists():
        await writer.write(
            await asyncio.to_thread(updater.output_csv.read_bytes),
            source_url=updater.output_csv.resolve().as_uri(),
            kind="prior_csv",
        )
    # Persist planning metadata independently of any source business rows.
    await writer.write(
        json.dumps(
            {
                "source": source,
                "start_year": start_year,
                "force": force,
                "fill_missing": fill_missing,
            }
        ).encode(),
        source_url=None,
        kind="collection_plan",
    )

    def download():
        crawler = updater.crawler_type(save_raw=False)
        try:
            if source == "hpsc_annual":
                years = _annual_years(
                    start_year=start_year,
                    force=force,
                    fill_missing=fill_missing,
                    existing_years=existing_years,
                )
                for report in crawler.discover_reports():
                    if set(report.years).intersection(years):
                        crawler.fetch_report(report)
            elif source == "hpsc_weekly_archive":
                start = max(
                    DEFAULT_ARCHIVE_START_YEAR,
                    min(
                        int(start_year or DEFAULT_ARCHIVE_START_YEAR),
                        DEFAULT_ARCHIVE_END[0],
                    ),
                )
                catalogue = tuple(
                    r
                    for r in crawler.discover_catalogue()
                    if r.year >= start and (r.year, r.week) <= DEFAULT_ARCHIVE_END
                )
                selected = {(r.year, r.week) for r in catalogue}
                if fill_missing and not force:
                    selected -= (
                        set(existing_weeks or ()) | updater._existing_source_periods()
                    )
                for report in catalogue:
                    if (report.year, report.week) in selected:
                        crawler._fetch_pdf(report)
            else:
                _weekly_transport(
                    crawler,
                    updater,
                    force=force,
                    fill_missing=fill_missing,
                    existing_weeks=existing_weeks,
                    start_year=start_year,
                )
        finally:
            crawler.session.close()

    with capture_writer(writer):
        await asyncio.to_thread(download)
    return writer.artifacts


def organize(
    artifacts,
    *,
    updater,
    source,
    force=False,
    fill_missing=False,
    start_year=None,
    existing_weeks=None,
    existing_years=None,
):
    clone = copy.copy(updater)
    clone.crawler_type = offline_crawler_type(updater.crawler_type, artifacts)
    original_csv = updater.output_csv
    options = {"force": force, "fill_missing": fill_missing, "start_year": start_year}
    if source == "hpsc_annual":
        options["existing_years"] = existing_years
    else:
        options["existing_weeks"] = existing_weeks
    with tempfile.TemporaryDirectory(prefix="globalid-ie-organize-") as directory:
        clone.output_csv = Path(directory) / original_csv.name
        previous = next((a for a in artifacts if a.kind == "prior_csv"), None)
        if previous:
            clone.output_csv.write_bytes(read_artifact(previous))
        result = clone.refresh_source(source=source, save_raw=False, **options)
    updates = {"source_csv": original_csv}
    if hasattr(result, "coverage_path"):
        updates["coverage_path"] = original_csv.with_suffix(".coverage.json")
    return replace(result, **updates)


async def run_pipeline(
    service,
    *,
    task,
    updater,
    source,
    force,
    process,
    fill_missing,
    start_year,
    get_database,
):
    from src.services.ingestion.runner import run_surveillance

    existing_weeks = existing_years = None
    if fill_missing and not force:
        async with get_database() as db:
            if source == "hpsc_annual":
                existing_years = await updater.get_db_years(db)
            else:
                existing_weeks = await updater.get_db_weeks(db)

    class Connector:
        @staticmethod
        async def collect(writer, *, source, **options):
            return await collect(writer, updater=updater, source=source, **options)

        @staticmethod
        def organize(artifacts, *, updater, source, **options):
            return organize(artifacts, updater=updater, source=source, **options)

    return await run_surveillance(
        service,
        task=task,
        updater=updater,
        connector=Connector,
        source=source,
        force=force,
        process=process,
        connector_options={
            "force": force,
            "fill_missing": fill_missing,
            "start_year": start_year,
            "existing_weeks": existing_weeks,
            "existing_years": existing_years,
        },
    )
