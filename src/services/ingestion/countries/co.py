"""Bounded SODA capture and offline organization for Colombia's INS history."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import hashlib
import json

from src.data.crawlers.co import (
    COUNT_URL, DEFAULT_SCOPE, HISTORY_START_YEAR, METADATA_URL, RECORD_URL,
    ColombiaINSSivigilaCrawler, ColombiaSIVIGILAContractError,
    count_query, page_query, parse_count, parse_metadata, parse_page,
)
from src.data.processors.co import ColombiaFetchResult, ColombiaINSSivigilaUpdater
from src.services.ingestion.artifacts import read_artifact

PAGE_LIMIT = 10_000


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, page_offset=0, page_limit=PAGE_LIMIT,
                  crawler_type=ColombiaINSSivigilaCrawler, **options):
    ColombiaINSSivigilaUpdater.normalize_source(source)
    year = int(start_year)
    if end_year is not None and int(end_year) != year:
        raise ColombiaSIVIGILAContractError("One source year required per bounded CO task")
    offset, limit = int(page_offset), int(page_limit)
    if not HISTORY_START_YEAR <= year <= date.today().year or offset < 0 or not 1 <= limit <= 20_000:
        raise ColombiaSIVIGILAContractError("Invalid bounded CO source window")
    crawler = crawler_type()
    try:
        before = await asyncio.to_thread(crawler.get, METADATA_URL)
        version = parse_metadata(before.content)
        await writer.write(before.content, source_url=before.url, kind="archive_json",
                           metadata={"endpoint": "metadata_before"})
        count_response = await asyncio.to_thread(crawler.get, COUNT_URL, params=count_query(year))
        expected_total = parse_count(count_response.content)
        await writer.write(count_response.content, source_url=count_response.url,
                           kind="archive_json", metadata={"endpoint": "year_count", "year": year})
        if not 0 <= offset < expected_total:
            raise ColombiaSIVIGILAContractError("Page offset exceeds official source year")
        page_response = await asyncio.to_thread(
            crawler.get, RECORD_URL, params=page_query(year, offset=offset, limit=limit))
        await writer.write(page_response.content, source_url=page_response.url,
                           kind="csv_page", metadata={"endpoint": "source_page", "year": year,
                           "offset": offset, "limit": limit,
                           "retrieved_at": datetime.now(timezone.utc).isoformat()})
        after = await asyncio.to_thread(crawler.get, METADATA_URL)
        if parse_metadata(after.content) != version:
            raise ColombiaSIVIGILAContractError("Official dataset changed during bounded page capture")
        await writer.write(after.content, source_url=after.url, kind="archive_json",
                           metadata={"endpoint": "metadata_after"})
        plan = {"year": year, "offset": offset, "limit": limit,
                "expected_year_rows": expected_total,
                "expected_page_rows": min(limit, expected_total - offset),
                "page_sha256": hashlib.sha256(page_response.content).hexdigest(),
                "page_url": page_response.url,
                "dataset_version": version, "public_release_enabled": False}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=METADATA_URL,
                           kind="archive_json", metadata={"endpoint": "collection_plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
             end_year=None, page_offset=None, **options):
    updater.normalize_source(source)
    def only(endpoint):
        found = [a for a in artifacts if a.metadata_.get("endpoint") == endpoint]
        if len(found) != 1:
            raise ColombiaSIVIGILAContractError(f"Exactly one captured {endpoint} required")
        return found[0]
    plan = json.loads(read_artifact(only("collection_plan")))
    year = int(plan["year"])
    if int(start_year) != year or (end_year is not None and int(end_year) != year):
        raise ColombiaSIVIGILAContractError("Requested CO year differs from captured plan")
    if page_offset is not None and int(page_offset) != plan["offset"]:
        raise ColombiaSIVIGILAContractError("Requested CO offset differs from captured plan")
    before, after = (parse_metadata(read_artifact(only(name)))
                     for name in ("metadata_before", "metadata_after"))
    if before != after or before != plan["dataset_version"]:
        raise ColombiaSIVIGILAContractError("Captured SODA metadata version differs from plan")
    year_count = parse_count(read_artifact(only("year_count")))
    if year_count != plan["expected_year_rows"]:
        raise ColombiaSIVIGILAContractError("Captured source year count differs from plan")
    page = only("source_page")
    if page.sha256 != plan["page_sha256"] or page.source_url != plan["page_url"]:
        raise ColombiaSIVIGILAContractError("Captured SODA page differs from plan")
    rows, detail = parse_page(read_artifact(page), year=year, offset=plan["offset"],
                              expected_rows=plan["expected_page_rows"],
                              source_url=page.source_url, dataset_version=before)
    audit = {"status": "partial_year" if plan["expected_page_rows"] != year_count else "parsed_year",
             "year": year, "year_source_rows": year_count, "page_offset": plan["offset"],
             "page_limit": plan["limit"], "captured_rows": len(rows),
             "dataset_version": before, "page": detail,
             "geography": "source municipality; unknown-location rows separate",
             "national_aggregate_derived": False, "public_release_enabled": False,
             "week_date_role": "synthetic ordering anchor; official boundaries unverified",
             "source_url": page.source_url}
    return ColombiaFetchResult(rows, max((date.fromisoformat(r["Date"]) for r in rows), default=None),
                               updater.output_csv,
                               [f"[CO] {len(rows)} of {year_count} source rows, year {year}, offset {plan['offset']}"],
                               audit)
