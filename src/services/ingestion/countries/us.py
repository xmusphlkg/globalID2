"""CDC collection downloads bytes; organization reads only those bytes."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.data.crawlers.us import (
    DEFAULT_CSV_API_URL, NNDSS_ARCHIVE_YEARS, SOCRATA_CATALOG_URL,
    SOCRATA_PAGE_SIZE, USNNDSSCrawler, USNHSSHIVCrawler,
)
from src.services.ingestion.artifacts import read_artifact


def mmwr_lower_bound(day: date) -> tuple[int, int]:
    """First source week ending on/after day, using the existing CDC calendar."""
    from src.data.processors.us import _mmwr_week_end_date
    day += timedelta(days=(5 - day.weekday()) % 7)
    year = day.year
    while day < _mmwr_week_end_date(year, 1):
        year -= 1
    while day >= _mmwr_week_end_date(year + 1, 1):
        year += 1
    delta = (day - _mmwr_week_end_date(year, 1)).days
    return year, (delta + 6) // 7 + 1


async def plan_collection(db, *, country_code, source, force=False, fill_missing=False):
    """Plan transport from persisted NNDSS facts, independently of annual NHSS.

    Normal refreshes revisit only the established eight-week revision window.
    Missing old years are repaired only on explicit fill_missing, or bootstrap
    when this geography has no NNDSS data at all. Holding/unmapped ledger rows
    participate without requiring a legacy disease mapping.
    """
    if normalize_source(source) == "nhss_hiv":
        return {}
    if force:
        return {"nndss_start": None, "archive_years": NNDSS_ARCHIVE_YEARS}
    from sqlalchemy import Integer, String, cast, func, select
    from src.domain import DiseaseSeriesObservation, DiseaseSurveillanceSeries
    from src.data.processors.us import NNDSS_MAPPING_SOURCE_ID, NNDSS_REVISION_LOOKBACK_WEEKS
    observation, series = DiseaseSeriesObservation, DiseaseSurveillanceSeries
    # TOTAL has a separate source geography and must never advance the US
    # resident checkpoint. Subdivisions use their own national geography key.
    ledger_filters = (
        series.source_system == NNDSS_MAPPING_SOURCE_ID,
        observation.geography_key == f"country:{country_code}:national",
    )
    ledger_latest = (await db.execute(
        select(func.max(observation.time))
        .join(series, series.series_code == observation.series_code)
        .where(*ledger_filters)
    )).scalar_one()
    latest = ledger_latest.date() if ledger_latest is not None else None
    if latest is None:
        return {"nndss_start": None, "archive_years": NNDSS_ARCHIVE_YEARS}
    revision_start = latest - timedelta(weeks=NNDSS_REVISION_LOOKBACK_WEEKS)
    lower = mmwr_lower_bound(revision_start)
    # A latest week in an old source archive still needs its revision window;
    # the current CSV does not contain the 2014–2021 historical datasets.
    archives = {year for year in NNDSS_ARCHIVE_YEARS if year >= lower[0]}
    if fill_missing:
        source_year = cast(func.coalesce(
            func.nullif(observation.raw_data["MMWRYear"].as_string(), ""),
            func.nullif(observation.raw_data["Current MMWR Year"].as_string(), ""),
            func.nullif(observation.raw_data["year"].as_string(), ""),
            func.nullif(observation.metadata_["mmwr_year"].as_string(), ""),
            cast(func.extract("year", observation.time), String),
        ), Integer)
        ledger_years = (await db.execute(
            select(source_year).distinct()
            .join(series, series.series_code == observation.series_code)
            .where(*ledger_filters)
        )).scalars().all()
        present = set(ledger_years)
        archives.update(set(NNDSS_ARCHIVE_YEARS) - present)
    return {"nndss_start": None if fill_missing else lower, "archive_years": tuple(sorted(archives))}


def csv_page_url(*, offset, nndss_start=None):
    parts = urlsplit(DEFAULT_CSV_API_URL)
    params = dict(parse_qsl(parts.query))
    if nndss_start is not None:
        year, week = nndss_start
        params["$where"] = (
            f"({params['$where']}) AND "
            f"(year::number > {int(year)} OR "
            f"(year::number = {int(year)} AND week::number >= {int(week)}))"
        )
    params.update({"$limit": SOCRATA_PAGE_SIZE, "$offset": offset})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


async def _check_cancelled(task_uuid):
    if task_uuid is not None:
        from src.core.task_manager import task_manager
        from src.services.exceptions import TaskCancelledError
        if await task_manager.is_cancel_requested(task_uuid):
            raise TaskCancelledError("Cancellation requested by user")
    # Give task cancellation a safe point even for explicit offline callers.
    await asyncio.sleep(0)


def normalize_source(source):
    source = str(source or "all").strip().lower()
    source = {"nndss": "nndss_api", "nhss": "nhss_hiv", "hiv": "nhss_hiv", "hiv_nhss": "nhss_hiv"}.get(source, source)
    if source not in {"all", "nndss_api", "nhss_hiv"}:
        raise ValueError(f"Unsupported US source: {source}")
    return source


async def collect(writer, *, source="all", subdivision=False, nndss_start=None, archive_years=None, task_uuid=None):
    source = normalize_source(source)
    if source in {"all", "nndss_api"}:
        crawler = USNNDSSCrawler()
        try:
            offset = 0
            while True:
                await _check_cancelled(task_uuid)
                url = csv_page_url(offset=offset, nndss_start=nndss_start)
                response = await asyncio.to_thread(crawler.get, url)
                await writer.write(response.content, source_url=url, kind="csv_page")
                await _check_cancelled(task_uuid)
                # Count records for transport pagination only; business parsing is offline.
                count = sum(1 for _ in csv.reader(io.StringIO(response.text))) - 1
                if count < SOCRATA_PAGE_SIZE:
                    break
                offset += SOCRATA_PAGE_SIZE
            for year in NNDSS_ARCHIVE_YEARS if archive_years is None else archive_years:
                await _check_cancelled(task_uuid)
                response = await asyncio.to_thread(crawler.get, SOCRATA_CATALOG_URL, params={"q": f"NNDSS - {year}", "domains": "data.cdc.gov", "limit": 1000})
                await writer.write(response.content, source_url=response.url, kind="archive_json", metadata={"catalog_year": year})
                await _check_cancelled(task_uuid)
                matches = {}
                title_re = re.compile(r"^NNDSS\s*-\s*(Table\s+[^\r\n]+?)\s*-\s*" + str(year) + r"\.", re.I)
                for result in response.json().get("results", []):
                    resource = result.get("resource", {})
                    match = title_re.search(str(resource.get("description") or ""))
                    if match:
                        key = " ".join(match[1].split()).casefold()
                        if key not in matches or str(resource.get("updatedAt") or "") > str(matches[key].get("updatedAt") or ""):
                            matches[key] = resource
                if not matches:
                    raise RuntimeError(f"[US-NNDSS] CDC archive catalog has no tables for {year}")
                for resource in matches.values():
                    if not resource.get("id") or not any(re.search(r",?\s*Current week\s*$", str(label).strip(), re.I) for label in resource.get("columns_name", [])):
                        continue
                    offset = 0
                    while True:
                        await _check_cancelled(task_uuid)
                        url = f"https://data.cdc.gov/resource/{resource['id']}.json"
                        response = await asyncio.to_thread(crawler.get, url, params={"$limit": SOCRATA_PAGE_SIZE, "$offset": offset})
                        await writer.write(response.content, source_url=response.url, kind="archive_json", metadata={"year": year, "resource": resource})
                        await _check_cancelled(task_uuid)
                        if len(response.json()) < SOCRATA_PAGE_SIZE:
                            break
                        offset += SOCRATA_PAGE_SIZE
        finally:
            crawler.session.close()
    if source in {"all", "nhss_hiv"} and not subdivision:
        await _check_cancelled(task_uuid)
        crawler = USNHSSHIVCrawler()
        try:
            # Discovery reads links only, without parsing surveillance observations.
            url = await asyncio.to_thread(crawler.discover_current_workbook_url)
            await _check_cancelled(task_uuid)
            response = await asyncio.to_thread(crawler.get, url)
            if not response.content:
                raise RuntimeError("[US-NHSS] Current HIV surveillance workbook is empty")
            await writer.write(response.content, source_url=url, kind="nhss_xlsx")
            await _check_cancelled(task_uuid)
        finally:
            crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, subdivision=False, nndss_start=None, archive_years=None, task_uuid=None):
    source = normalize_source(source)
    nndss_rows, archive_refs = [], {}
    nhss = None
    archive_counts = {}
    for artifact in artifacts:
        payload = read_artifact(artifact)
        if artifact.kind == "csv_page":
            nndss_rows.extend(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        elif artifact.kind == "nhss_xlsx":
            nhss = (USNHSSHIVCrawler.parse_current_workbook(payload, source_url=artifact.source_url), artifact.source_url)
        elif artifact.kind == "archive_json" and "resource" in artifact.metadata_:
            year = artifact.metadata_["year"]
            resource = artifact.metadata_["resource"]
            rows = USNNDSSCrawler.parse_archive_table(resource, json.loads(payload), year)
            nndss_rows.extend(rows)
            archive_counts[year] = archive_counts.get(year, 0) + len(rows)
            archive_refs.setdefault(year, {})[resource["id"]] = f"https://data.cdc.gov/d/{resource['id']}"
    for year, count in archive_counts.items():
        if not count:
            raise RuntimeError(f"[US-NNDSS] CDC archive tables for {year} contained no usable rows")
    refs = [DEFAULT_CSV_API_URL] + [ref for tables in archive_refs.values() for ref in tables.values()]
    nndss = (nndss_rows, " | ".join(refs)) if source in {"all", "nndss_api"} else None
    return updater.organize_sources(source, nndss=nndss, nhss=nhss)
