"""Capture unauthenticated aggregate endpoints and replay without network access."""
import asyncio
import json

import requests

from src.data.crawlers.np_sormas import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, WEEKS_URL, NepalSORMASCrawler,
    NepalSORMASContractError, discover_weeks, parse_bulletin,
)
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, max_reports=200, crawler_type=NepalSORMASCrawler, **options):
    from src.data.processors.np_sormas import NepalSORMASUpdater
    NepalSORMASUpdater.normalize_source(source)
    crawler = crawler_type()
    try:
        response = await asyncio.to_thread(crawler.get, WEEKS_URL)
        await writer.write(response.content, source_url=response.url, kind="archive_json",
                           metadata={"endpoint": "weeks"})
        reports = discover_weeks(response.content, start_year=start_year, end_year=end_year or 9999)
        if not reports or len(reports) > int(max_reports):
            raise NepalSORMASContractError("Empty or oversized bulletin window; select explicit years")
        failures = []
        for report in reports:
            try:
                response = await asyncio.to_thread(crawler.get, report["url"])
            except requests.RequestException as exc:
                failures.append({**report, "error": str(exc)})
                continue
            await writer.write(response.content, source_url=response.url, kind="archive_json",
                               metadata={"endpoint": "bulletin", "report": report})
        await writer.write(json.dumps({"reports": reports, "failed_downloads": failures,
            "requested_start_year": int(start_year), "requested_end_year": end_year}).encode(),
            source_url=WEEKS_URL, kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise NepalSORMASContractError("Exactly one public bulletin plan required")
    plan = json.loads(read_artifact(plans[0]))
    expected = {r["url"]: r for r in plan["reports"]}
    rows, parsed, seen = [], [], set()
    failures = list(plan["failed_downloads"])
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "bulletin":
            continue
        if artifact.source_url not in expected or artifact.source_url in seen:
            raise NepalSORMASContractError("Unlinked or duplicate public bulletin artifact")
        seen.add(artifact.source_url)
        payload = read_artifact(artifact)
        try:
            observations, detail = parse_bulletin(payload, expected[artifact.source_url])
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({"url": artifact.source_url, "stage": "parse", "error": str(exc)})
            continue
        rows.extend(observations)
        parsed.append(detail)
    for url in expected.keys() - seen:
        if not any(item.get("url") == url for item in failures):
            failures.append({"url": url, "stage": "missing_artifact"})
    audit = {"status": "partial" if failures else "captured_selected_weeks",
             "reports": parsed, "expected_reports": len(expected), "failed_reports": failures,
             "authoritative_window": False, "calendar_status": "unverified_source_week_boundaries",
             "coverage_note": "Published source weeks only; national reporting-network aggregate and provinces remain separate. No inferred weeks, deaths, cumulative totals or case classification."}
    return updater.prepare_rows(rows, source=source, source_audit=audit,
                                start_year=plan["requested_start_year"])
