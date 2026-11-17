"""Capture linked MoPH NDSR originals and replay without network access."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import requests

from src.data.crawlers.af import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, INDEX_URL, SEED_PDF_URL, WHO_INDEX_URL,
    AfghanistanNDSRCrawler, AfghanistanNDSRContractError, discover_reports,
    official_pdf_url, parse_report_pdf,
)
from src.data.crawlers.za_cache import NICDReportCache
from src.data.processors.af import AfghanistanNDSRUpdater
from src.services.ingestion.artifacts import read_artifact


def _seed_reports(report_urls, *, start_year, end_year):
    import re
    from urllib.parse import unquote, urlparse
    result = []
    for url in report_urls:
        url = official_pdf_url(url)
        filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        years = re.findall(r"(?<!\d)20\d{2}(?!\d)", filename)
        year = int(years[-1]) if years else None
        if year is not None and not int(start_year) <= year <= int(end_year):
            continue
        result.append({"pdf_url": url, "post_url": "", "title": filename,
                       "listed_year": year, "discovery": "explicit_report_url"})
    return result


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, report_urls=None, max_reports=12, max_directories=12,
                  cache_ttl_hours=24, force_refresh=False,
                  crawler_type=AfghanistanNDSRCrawler, **options):
    AfghanistanNDSRUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 260 or not 1 <= int(max_directories) <= 100:
        raise AfghanistanNDSRContractError("Invalid bounded NDSR discovery limits")
    upper = int(end_year or datetime.now(timezone.utc).year)
    if int(start_year) > upper:
        raise AfghanistanNDSRContractError("Requested NDSR year interval is empty")
    explicit_urls = [SEED_PDF_URL] if report_urls is None else list(report_urls)
    explicit_reports = _seed_reports(explicit_urls, start_year=start_year, end_year=upper)
    reports = {report["pdf_url"]: report for report in explicit_reports}
    crawler = crawler_type()
    cache = NICDReportCache(Path("data/cache/af/ndsr_weekly"))
    pending, seen, failures = [INDEX_URL, WHO_INDEX_URL], set(), []
    try:
        while pending and len(seen) < int(max_directories):
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except requests.RequestException as exc:
                failures.append({"url": url, "stage": "directory", "error": str(exc)})
                continue
            await writer.write(response.content, source_url=url, kind="html_report",
                               metadata={"endpoint": "directory", "retrieved_at": datetime.now(timezone.utc).isoformat()})
            try:
                directories, linked = discover_reports(response.content, url, start_year=start_year, end_year=upper)
            except (ValueError, TypeError) as exc:
                failures.append({"url": url, "stage": "directory_parse", "error": str(exc)})
                continue
            pending.extend(item for item in directories if item not in seen and item not in pending)
            for report in linked:
                reports[report["pdf_url"]] = report
        selected = sorted(reports.values(), key=lambda r: (r["listed_year"] or 0, r["pdf_url"]), reverse=True)[:int(max_reports)]
        for report in selected:
            try:
                payload, receipt = await asyncio.to_thread(
                    cache.get, crawler, report["pdf_url"], ttl_hours=cache_ttl_hours,
                    force_refresh=force_refresh or bool(options.get("force")))
                if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                    raise AfghanistanNDSRContractError("MoPH link did not return a complete PDF")
            except (requests.RequestException, ValueError) as exc:
                failures.append({"url": report["pdf_url"], "stage": "pdf", "error": str(exc)})
                continue
            await writer.write(payload, source_url=report["pdf_url"], kind="cda_pdf",
                               metadata={"endpoint": "report", "report": report, **receipt})
        plan = {"reports": selected, "explicit_urls": explicit_urls, "failures": failures,
                "directories": sorted(seen), "pending_directories": sorted(set(pending) - seen),
                "max_reports": int(max_reports), "discovered_reports": len(reports),
                "start_year": int(start_year), "end_year": upper,
                "captured_at": datetime.now(timezone.utc).isoformat()}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=INDEX_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_documents(documents, *, expected_reports, failed_downloads,
                       start_year=HISTORY_START_YEAR, end_year=None):
    expected = {report["pdf_url"]: report for report in expected_reports}
    if len(expected) != len(expected_reports):
        raise AfghanistanNDSRContractError("Duplicate report in NDSR plan")
    rows, seen, report_audit = [], set(), []
    for report, payload in documents:
        url = report["pdf_url"]
        if url not in expected or report != expected[url] or url in seen:
            raise AfghanistanNDSRContractError("Unplanned, mismatched or duplicated NDSR original")
        seen.add(url)
        try:
            parsed, detail = parse_report_pdf(payload, report)
        except (ValueError, RuntimeError) as exc:
            report_audit.append({"url": url, "status": "unparsed", "error": str(exc)})
            continue
        if detail["year"] < int(start_year) or (end_year is not None and detail["year"] > int(end_year)):
            report_audit.append({"url": url, "status": "outside_requested_years", **detail})
            continue
        rows.extend(parsed)
        report_audit.append({"url": url, "status": "parsed_candidate_contract", **detail})
    failed = {item["url"] for item in failed_downloads if item["stage"] == "pdf"}
    if set(expected) - seen != failed or seen & failed:
        raise AfghanistanNDSRContractError("NDSR capture plan incomplete without explicit download failure")
    grouped = {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["DefinitionVersion"])
        grouped.setdefault(key, []).append(row)
    unique, conflicts = [], []
    for key, values in grouped.items():
        if len({(r["Cases"], r["MissingValueStatus"], r["PeriodStart"], r["PeriodEnd"]) for r in values}) != 1:
            conflicts.append({"identity": list(key), "urls": [r["SourceURL"] for r in values]})
        else:
            unique.append(values[0])
    unique.sort(key=lambda r: (r["Date"], r["SourceDiseaseCode"]))
    return unique, {"status": "partial", "coverage_complete": False,
        "numerical_contract_live_verified": False, "expected_reports": len(expected),
        "captured_reports": len(seen), "reports": report_audit,
        "failures": failed_downloads, "conflicts": conflicts, "observations": len(unique),
        "coverage_note": "MoPH NDSR originals only; no complete national incidence claim, inferred calendar, cumulative/outbreak count, laboratory or province/district extraction."}


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise AfghanistanNDSRContractError("Exactly one captured NDSR plan required")
    plan = json.loads(read_artifact(plans[0]))
    discovered = {r["pdf_url"]: r for r in _seed_reports(plan["explicit_urls"], start_year=plan["start_year"], end_year=plan["end_year"])}
    seen_directories = set()
    parse_failures = {item["url"] for item in plan["failures"] if item["stage"] == "directory_parse"}
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "directory":
            continue
        if artifact.source_url in seen_directories:
            raise AfghanistanNDSRContractError("Duplicate captured NDSR directory")
        seen_directories.add(artifact.source_url)
        if artifact.source_url in parse_failures:
            continue
        _, reports = discover_reports(read_artifact(artifact), artifact.source_url,
                                      start_year=plan["start_year"], end_year=plan["end_year"])
        discovered.update({r["pdf_url"]: r for r in reports})
    directory_failures = {item["url"] for item in plan["failures"] if item["stage"] == "directory"}
    if set(plan["directories"]) - seen_directories != directory_failures or seen_directories - set(plan["directories"]):
        raise AfghanistanNDSRContractError("Captured NDSR directory chain is incomplete")
    selected = sorted(discovered.values(), key=lambda r: (r["listed_year"] or 0, r["pdf_url"]), reverse=True)[:int(plan["max_reports"])]
    if selected != plan["reports"]:
        raise AfghanistanNDSRContractError("NDSR plan differs from captured links and explicit URLs")
    expected = {r["pdf_url"]: r for r in selected}
    documents = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "report":
            continue
        report = artifact.metadata_["report"]
        if artifact.source_url not in expected or report != expected[artifact.source_url]:
            raise AfghanistanNDSRContractError("NDSR original metadata does not match plan")
        documents.append((report, read_artifact(artifact)))
    rows, audit = organize_documents(documents, expected_reports=selected,
        failed_downloads=plan["failures"], start_year=plan["start_year"], end_year=plan["end_year"])
    audit.update(discovered_reports=plan["discovered_reports"],
                 pending_directories=plan["pending_directories"],
                 requested_start_year=plan["start_year"], requested_end_year=plan["end_year"],
                 captured_at=plan["captured_at"])
    return updater.prepare_rows(rows, source=source, start_year=plan["start_year"], source_audit=audit)
