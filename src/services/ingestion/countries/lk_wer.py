"""Capture linked WER originals with immutable manifests and offline inspection."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

import requests

from src.data.crawlers.lk_wer import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, INDEX_URL, SriLankaWERContractError,
    SriLankaWERCrawler, discover_archive_links, discover_reports, inspect_report_pdf,
    official_url, wayback_url, WAYBACK_CDX_URL, discover_wayback_reports, parse_report_pdf,
)
from src.data.processors.lk_wer import SriLankaWERUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, max_reports=8, max_index_pages=10,
                  crawler_type=SriLankaWERCrawler, **options):
    SriLankaWERUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 1000 or not 1 <= int(max_index_pages) <= 100:
        raise ValueError("WER collection bounds must be 1..1000 reports and 1..100 archive pages")
    crawler = crawler_type()
    queue, seen, reports, failures, manifest = [INDEX_URL], set(), {}, [], []
    try:
        while queue and len(seen) < int(max_index_pages):
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except requests.RequestException as exc:
                response = exc.response
                if response is not None:
                    await writer.write(response.content, source_url=url, kind="html_report",
                        metadata={"endpoint": "index_error", "http_status": response.status_code})
                failures.append({"url": url, "stage": "index_download", "error": str(exc)})
                continue
            await writer.write(response.content, source_url=url, kind="html_report",
                metadata={"endpoint": "index", "retrieved_at": datetime.now(timezone.utc).isoformat()})
            try:
                if not official_url(response.url):
                    raise SriLankaWERContractError("Archive redirected outside the official host")
                targets = discover_reports(response.content, source_url=response.url,
                    start_year=start_year, end_year=end_year)
                for target in targets:
                    reports[target["pdf_url"]] = target
                queue.extend(link for link in discover_archive_links(response.content,
                    source_url=response.url) if link not in seen and link not in queue)
            except SriLankaWERContractError as exc:
                failures.append({"url": url, "stage": "index_contract", "error": str(exc)})
        targets = sorted(reports.values(), key=lambda item: item["pdf_url"])
        selected = targets[:int(max_reports)]
        for report in selected:
            try:
                response = await asyncio.to_thread(crawler.get, report["pdf_url"])
            except requests.RequestException as exc:
                failures.append({"url": report["pdf_url"], "stage": "pdf_download", "error": str(exc)})
                continue
            # Store every received original before checking PDF format or scope.
            await writer.write(response.content, source_url=report["pdf_url"], kind="cda_pdf",
                metadata={"endpoint": "pdf", "report": report, "response_url": response.url})
            if not official_url(response.url):
                failures.append({"url": report["pdf_url"], "stage": "pdf_contract", "error": "Nonofficial redirect"})
                continue
            manifest.append({"url": report["pdf_url"], "sha256": hashlib.sha256(response.content).hexdigest()})
        plan = {"targets": targets, "selected": selected, "manifest": manifest, "failures": failures,
                "requested_start_year": int(start_year), "requested_end_year": end_year,
                "remaining_archive_pages": len(queue), "reports_truncated": len(targets) > len(selected),
                "public_release_enabled": False, "numerical_contract_verified": False,
                "authoritative_replacement": False}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=INDEX_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        if not manifest:
            raise SriLankaWERContractError("No official WER originals captured; inspect persisted collection plan: "
                                           + json.dumps(failures))
        return writer.artifacts
    finally:
        crawler.session.close()


async def collect_wayback(writer, *, source=DEFAULT_SCOPE, start_year=2007,
                          end_year=2024, max_reports=104, crawler_type=SriLankaWERCrawler, **options):
    """Capture historical official WER PDFs from the public Wayback CDX index."""
    SriLankaWERUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 500:
        raise ValueError("Wayback WER max_reports must be 1..500")
    crawler = crawler_type()
    try:
        query = (WAYBACK_CDX_URL + "?url=www.epid.gov.lk/web/images/pdf/wer/*"
                 "&output=json&filter=statuscode:200&collapse=urlkey&limit=1000")
        response = await asyncio.to_thread(crawler.get, query)
        await writer.write(response.content, source_url=query, kind="archive_json",
                           metadata={"endpoint": "wayback_cdx", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        reports = discover_wayback_reports(response.content, start_year=start_year, end_year=end_year)
        selected = reports[:int(max_reports)]
        failures = []
        manifest = []
        for report in selected:
            try:
                replay = await asyncio.to_thread(crawler.get, report["pdf_url"])
            except requests.RequestException as exc:
                failures.append({"url": report["pdf_url"], "error": str(exc)})
                continue
            await writer.write(replay.content, source_url=report["pdf_url"], kind="cda_pdf",
                metadata={"endpoint": "wayback_pdf", "report": report,
                          "response_url": replay.url, "archive_timestamp": report["archive_timestamp"]})
            manifest.append({"url": report["pdf_url"], "sha256": hashlib.sha256(replay.content).hexdigest(),
                             "original_url": report["original_url"], "archive_digest": report["archive_digest"]})
        plan = {"targets": reports, "selected": selected, "manifest": manifest, "failures": failures,
                "requested_start_year": int(start_year), "requested_end_year": int(end_year),
                "reports_truncated": len(reports) > len(selected), "public_release_enabled": False,
                "provenance": "Wayback CDX capture of official epid.gov.lk originals"}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=query,
                           kind="archive_json", metadata={"endpoint": "wayback_plan"})
        if not manifest:
            raise SriLankaWERContractError("No archived WER originals captured")
        return writer.artifacts
    finally:
        crawler.session.close()


def inspect_artifacts(artifacts):
    plans = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") in {"plan", "wayback_plan"}]
    if len(plans) != 1:
        raise SriLankaWERContractError("Exactly one WER collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    allowed = {target["pdf_url"] for target in plan["selected"]}
    reports = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") in {"pdf", "wayback_pdf"}]
    actual = [{"url": artifact.source_url, "sha256": artifact.sha256} for artifact in reports]
    expected_manifest = [{"url": item["url"], "sha256": item["sha256"]} for item in plan["manifest"]]
    if sorted(actual, key=lambda item: item["url"]) != sorted(expected_manifest, key=lambda item: item["url"]):
        raise SriLankaWERContractError("WER captured PDFs differ from immutable collection manifest")
    inspections = []
    for artifact in reports:
        if artifact.source_url not in allowed or not (official_url(artifact.metadata_.get("response_url", artifact.source_url))
                                                     or wayback_url(artifact.metadata_.get("response_url", artifact.source_url))):
            raise SriLankaWERContractError("Unlinked or nonofficial WER PDF artifact")
        payload = read_artifact(artifact)
        try:
            inspections.append(inspect_report_pdf(payload, source_url=artifact.metadata_.get("report", {}).get("original_url", artifact.source_url)))
        except Exception as exc:
            inspections.append({"source_url": artifact.source_url, "status": "unparsed", "error": str(exc)})
    return {**plan, "reports": inspections, "observations": 0,
            "status": "numerical_contract_unverified", "missing_reports_are_not_zero": True}


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    audit = inspect_artifacts(artifacts)
    rows = []
    parse_errors = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") not in {"pdf", "wayback_pdf"}:
            continue
        report = dict(artifact.metadata_.get("report", {}))
        report.setdefault("pdf_url", artifact.source_url)
        try:
            parsed, detail = parse_report_pdf(read_artifact(artifact), report)
        except Exception as exc:
            parse_errors.append({"url": artifact.source_url, "error": str(exc)})
        else:
            rows.extend(parsed)
    if not rows:
        raise SriLankaWERContractError("no observations from validated WER Table 5: " + json.dumps(parse_errors))
    audit.update(status="validated_legacy_table5", parse_errors=parse_errors,
                 parsed_rows=len(rows), failed_reports=parse_errors,
                 missing_reports_are_not_zero=True, public_release_enabled=False)
    return updater.prepare_rows(rows, source=source, source_audit=audit)
