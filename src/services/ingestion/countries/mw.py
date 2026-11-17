"""Capture PHIM bulletin originals before offline national series extraction."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.data.crawlers.mw import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, MEDIA_URL, SEARCH_URL, MalawiIDSRContractError,
    MalawiPHIMIDSRCrawler, discover_media, discover_pdf, discover_posts, parse_report_pdf,
)
from src.services.ingestion.artifacts import read_artifact
from src.data.crawlers.za_cache import NICDReportCache


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, cache_ttl_hours=24, force_refresh=False,
                  crawler_type=MalawiPHIMIDSRCrawler, **options):
    from src.data.processors.mw import MalawiPHIMIDSRUpdater
    MalawiPHIMIDSRUpdater.normalize_source(source)
    crawler = crawler_type()
    cache = NICDReportCache(directory=Path("data/cache/mw/phim_idsr_reports"))
    failures = []
    reports = []
    media_reports = []
    try:
        response = await asyncio.to_thread(crawler.get, SEARCH_URL)
        pages = int(response.headers.get("X-WP-TotalPages", "1"))
        if pages < 1 or pages > 20:
            raise MalawiIDSRContractError("Unexpected PHIM search pagination")
        posts = []
        for page in range(1, pages + 1):
            if page > 1:
                url = re.sub(r"page=\d+", f"page={page}", SEARCH_URL)
                response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=response.url, kind="archive_json",
                               metadata={"endpoint": "search", "page": page,
                                         "total_pages": pages, "retrieved_at": datetime.now(timezone.utc).isoformat()})
            posts.extend(discover_posts(response.content))
        posts = list({post["post_id"]: post for post in posts}.values())
        if not posts:
            raise MalawiIDSRContractError("PHIM search contains no IDSR weekly bulletin posts")
        for post in posts:
            try:
                response = await asyncio.to_thread(crawler.get, post["post_url"])
            except requests.RequestException as exc:
                failures.append({**post, "stage": "post", "error": str(exc)})
                continue
            await writer.write(response.content, source_url=response.url, kind="html_report",
                               metadata={"endpoint": "post", **post})
            try:
                report = discover_pdf(response.content, post)
            except MalawiIDSRContractError as exc:
                failures.append({**post, "stage": "post_parse", "error": str(exc)})
                continue
            path = urlparse(report["pdf_url"]).path
            match = re.search(r"/wp-content/uploads/(20\d{2})/", path)
            if match and not (int(start_year) <= int(match[1]) <= int(end_year or 9999)):
                continue
            reports.append(report)
        response = await asyncio.to_thread(crawler.get, MEDIA_URL)
        media_pages = int(response.headers.get("X-WP-TotalPages", "1"))
        if media_pages < 1 or media_pages > 20:
            raise MalawiIDSRContractError("Unexpected PHIM media pagination")
        for page in range(1, media_pages + 1):
            if page > 1:
                url = re.sub(r"page=\d+", f"page={page}", MEDIA_URL)
                response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=response.url, kind="archive_json",
                               metadata={"endpoint": "media", "page": page,
                                         "total_pages": media_pages,
                                         "retrieved_at": datetime.now(timezone.utc).isoformat()})
            media_reports.extend(discover_media(response.content, start_year=start_year, end_year=end_year))
        # Linked posts carry the more useful public listing URL when the same
        # PDF also appears in the official media catalogue.
        reports = list({report["pdf_url"]: report for report in media_reports + reports}.values())
        if not reports:
            raise MalawiIDSRContractError("No official PHIM IDSR PDF found in requested window")
        for report in reports:
            try:
                payload, receipt = await asyncio.to_thread(
                    cache.get, crawler, report["pdf_url"], ttl_hours=cache_ttl_hours,
                    force_refresh=force_refresh or bool(options.get("force")))
                if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                    raise MalawiIDSRContractError("Incomplete PHIM PDF response")
            except (requests.RequestException, MalawiIDSRContractError) as exc:
                failures.append({**report, "stage": "pdf", "error": str(exc)})
                continue
            await writer.write(payload, source_url=report["pdf_url"], kind="cda_pdf",
                               metadata={"endpoint": "pdf", "report": report,
                                         "retrieved_at": receipt["retrieved_at"],
                                         "revalidated_at": receipt["revalidated_at"],
                                         "cache_status": receipt["cache_status"]})
        await writer.write(json.dumps({"posts": posts, "reports": reports,
                                       "media_reports": len(media_reports),
                                       "failed_downloads": failures,
                                       "requested_start_year": int(start_year), "requested_end_year": end_year,
                                       "discovery_url": SEARCH_URL, "media_discovery_url": MEDIA_URL,
                                       "cache_ttl_hours": cache_ttl_hours}, sort_keys=True).encode(),
                           source_url=SEARCH_URL, kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_documents(documents, *, expected_reports, failed_downloads,
                       start_year=HISTORY_START_YEAR, end_year=None):
    rows = []
    audit = {"status": "partial", "reports": [], "expected_reports": len(expected_reports),
             "failed_downloads": list(failed_downloads), "discovery_url": SEARCH_URL}
    for report, payload in documents:
        try:
            parsed, detail = parse_report_pdf(payload, report)
        except Exception as exc:
            audit["reports"].append({"url": report["pdf_url"], "status": "unparsed", "error": str(exc)})
            continue
        if detail["year"] < int(start_year) or (end_year is not None and detail["year"] > int(end_year)):
            audit["reports"].append({"url": report["pdf_url"], "status": "outside_requested_years",
                                     **detail})
            continue
        if (report.get("associated_post_year"), report.get("associated_post_week")) == (detail["year"], detail["week"]):
            for row in parsed:
                row["SourceListingURL"] = report["associated_post_url"]
            detail["associated_post_url"] = report["associated_post_url"]
        rows.extend(parsed)
        audit["reports"].append({"url": report["pdf_url"], "status": "parsed",
                                 "observations": len(parsed), **detail})
    selected = {}
    conflicts = {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["DefinitionVersion"])
        previous = selected.get(key)
        if previous is None:
            selected[key] = row
        elif previous["Cases"] != row["Cases"]:
            conflicts.setdefault(key, [previous["SourceURL"]]).append(row["SourceURL"])
    rows = [row for key, row in selected.items() if key not in conflicts]
    rows.sort(key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
    audit.update(captured_reports=len(audit["reports"]),
                 parsed_reports=sum(item["status"] == "parsed" for item in audit["reports"]),
                 unparsed_reports=sum(item["status"] == "unparsed" for item in audit["reports"]),
                 conflicting_observations=[{"identity": list(key), "urls": urls} for key, urls in conflicts.items()],
                 observations=len(rows),
                 coverage_note="National Table 1 only. Missing weeks, split or ambiguous cells, annex districts, figures and outbreak cumulative narratives are not inferred as zero or weekly case counts.")
    return rows, audit


def _associate_media_posts(reports):
    linked = {}
    for report in reports:
        if "post_id" not in report:
            continue
        path = urlparse(report["pdf_url"]).path
        year = re.search(r"/uploads/(20\d{2})/", path)
        week = re.search(r"week[-_]*(\d{1,2})(?!\d)", path, re.I)
        if year and week:
            linked.setdefault((int(year[1]), int(week[1])), []).append(report)
    for report in reports:
        if "media_id" not in report:
            continue
        path = urlparse(report["pdf_url"]).path
        year = re.search(r"/uploads/(20\d{2})/", path)
        week = re.search(r"week[-_]*(\d{1,2})(?!\d)", path, re.I)
        matches = linked.get((int(year[1]), int(week[1])), []) if year and week else []
        if len(matches) == 1:
            report.update(associated_post_url=matches[0]["post_url"],
                          associated_post_year=int(year[1]), associated_post_week=int(week[1]))


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise MalawiIDSRContractError("Exactly one PHIM discovery plan required")
    plan = json.loads(read_artifact(plans[0]))
    _associate_media_posts(plan["reports"])
    expected = {report["pdf_url"]: report for report in plan["reports"]}
    documents = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "pdf":
            continue
        report = artifact.metadata_["report"]
        if artifact.source_url not in expected or artifact.source_url != report["pdf_url"]:
            raise MalawiIDSRContractError("Unlinked PHIM PDF artifact")
        documents.append((expected[artifact.source_url], read_artifact(artifact)))
    rows, audit = organize_documents(documents, expected_reports=plan["reports"],
                                      failed_downloads=plan["failed_downloads"],
                                      start_year=plan["requested_start_year"],
                                      end_year=plan["requested_end_year"])
    audit["requested_start_year"] = plan["requested_start_year"]
    audit["requested_end_year"] = plan["requested_end_year"]
    audit["media_reports"] = plan.get("media_reports", 0)
    audit["media_discovery_url"] = plan.get("media_discovery_url")
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
