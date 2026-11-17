"""Capture the WHO Afghanistan archive and replay exact weekly matrix cells."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import requests

from src.data.crawlers.af import AfghanistanNDSRContractError
from src.data.crawlers.af_who_outbreaks import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, INDEX_URL, AfghanistanWHOOutbreaksCrawler,
    discover_reports, parse_report_pdf,
)
from src.data.crawlers.za_cache import NICDReportCache
from src.data.processors.af_who_outbreaks import AfghanistanWHOOutbreaksUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, max_reports=12, weeks=None, cache_ttl_hours=24, force_refresh=False,
                  crawler_type=AfghanistanWHOOutbreaksCrawler, download_concurrency=4, **options):
    AfghanistanWHOOutbreaksUpdater.normalize_source(source)
    upper = int(end_year or datetime.now(timezone.utc).year)
    if not 1 <= int(download_concurrency) <= 8:
        raise AfghanistanNDSRContractError("WHO report download concurrency must be between 1 and 8")
    crawler = crawler_type()
    cache = NICDReportCache(Path("data/cache/af/who_outbreaks_weekly"))
    failures = []
    try:
        response = await asyncio.to_thread(crawler.get, INDEX_URL)
        await writer.write(response.content, source_url=INDEX_URL, kind="html_report",
                           metadata={"endpoint": "directory", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        reports, link_conflicts = discover_reports(response.content, start_year=start_year, end_year=upper, max_reports=max_reports, weeks=weeks, return_conflicts=True)
        if not reports:
            raise AfghanistanNDSRContractError("No linked WHO Afghanistan outbreak PDF in requested years")
        parallel = asyncio.Semaphore(int(download_concurrency))
        async def capture_report(report):
            async with parallel:
                client = crawler_type()
                try:
                    try:
                        payload, receipt = await asyncio.to_thread(
                            cache.get, client, report["pdf_url"], ttl_hours=cache_ttl_hours,
                            force_refresh=force_refresh or bool(options.get("force")))
                        if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                            raise AfghanistanNDSRContractError("WHO link did not return a complete PDF")
                    except (requests.RequestException, ValueError) as exc:
                        failures.append({"url": report["pdf_url"], "stage": "pdf", "error": str(exc)})
                        return
                    await writer.write(payload, source_url=report["pdf_url"], kind="cda_pdf",
                                       metadata={"endpoint": "report", "report": report, **receipt})
                finally:
                    client.session.close()
        await asyncio.gather(*(capture_report(report) for report in reports))
        plan = {"reports": reports, "link_conflicts": link_conflicts, "failures": failures, "max_reports": max_reports, "weeks": weeks,
                "start_year": int(start_year), "end_year": upper,
                "captured_at": datetime.now(timezone.utc).isoformat()}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=INDEX_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_documents(documents, *, expected_reports, failed_downloads):
    expected = {report["pdf_url"]: report for report in expected_reports}
    if len(expected) != len(expected_reports):
        raise AfghanistanNDSRContractError("Duplicate WHO outbreak report in plan")
    seen, rows, audit_reports = set(), [], []
    for report, payload in documents:
        url = report["pdf_url"]
        if url not in expected or expected[url] != report or url in seen:
            raise AfghanistanNDSRContractError("Unlinked, changed or duplicated WHO outbreak original")
        seen.add(url)
        try:
            parsed, detail = parse_report_pdf(payload, report)
        except (ValueError, RuntimeError) as exc:
            audit_reports.append({"url": url, "status": "unparsed", "error": str(exc)})
            continue
        rows.extend(parsed)
        audit_reports.append({"url": url, "status": "parsed", **detail})
    failed = {item["url"] for item in failed_downloads if item["stage"] == "pdf"}
    if set(expected) - seen != failed or seen & failed:
        raise AfghanistanNDSRContractError("Missing WHO outbreak original without explicit download failure")
    grouped = {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["DefinitionVersion"])
        grouped.setdefault(key, []).append(row)
    unique, conflicts = [], []
    for key, values in grouped.items():
        if len({(r["Cases"], r["PeriodStart"], r["PeriodEnd"], r["MissingValueStatus"]) for r in values}) != 1:
            conflicts.append({"identity": list(key), "urls": [r["SourceURL"] for r in values]})
        else:
            unique.append(values[0])
    unique.sort(key=lambda r: (r["Date"], r["SourceDiseaseCode"]))
    return unique, {"status": "partial", "coverage_complete": False, "numerical_contract_live_verified": True,
        "discovered_reports": len(expected), "captured_reports": len(seen), "reports": audit_reports,
        "failures": failed_downloads, "conflicts": conflicts, "observations": len(unique),
        "coverage_note": "Current-week exact matrix cells only. Unreadable/rotated tables, historical columns and revision notes, charts, cumulative counts, province/district breakdowns and narratives remain explicit coverage gaps."}


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    directories = [a for a in artifacts if a.metadata_.get("endpoint") == "directory"]
    if len(plans) != 1 or len(directories) != 1 or directories[0].source_url != INDEX_URL:
        raise AfghanistanNDSRContractError("Exactly one official WHO directory and collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    linked, link_conflicts = discover_reports(read_artifact(directories[0]), start_year=plan["start_year"],
                              end_year=plan["end_year"], max_reports=plan["max_reports"], weeks=plan.get("weeks"), return_conflicts=True)
    if linked != plan["reports"]:
        raise AfghanistanNDSRContractError("WHO outbreak plan differs from captured official links")
    expected = {r["pdf_url"]: r for r in linked}
    documents = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "report":
            continue
        report = artifact.metadata_["report"]
        if artifact.source_url not in expected or report != expected[artifact.source_url]:
            raise AfghanistanNDSRContractError("WHO original metadata differs from captured plan")
        documents.append((report, read_artifact(artifact)))
    rows, audit = organize_documents(documents, expected_reports=linked, failed_downloads=plan["failures"])
    audit.update(captured_at=plan["captured_at"], requested_start_year=plan["start_year"], requested_end_year=plan["end_year"],
                 link_conflicts=plan.get("link_conflicts", link_conflicts))
    return updater.prepare_rows(rows, source=source, start_year=plan["start_year"], source_audit=audit)
