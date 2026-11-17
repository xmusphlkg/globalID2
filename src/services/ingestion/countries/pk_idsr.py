"""Bounded NIH report capture followed by fully offline organization."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

import requests

from src.data.crawlers.pk_idsr import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, INDEX_URL, PakistanIDSRContractError,
    PakistanNIHIDSRCrawler, discover_reports, parse_report_pdf,
)
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, weeks=None, max_reports=12,
                  crawler_type=PakistanNIHIDSRCrawler, **options):
    from src.data.processors.pk_idsr import PakistanNIHIDSRUpdater
    PakistanNIHIDSRUpdater.normalize_source(source)
    crawler = crawler_type()
    failures = []
    manifest = []
    try:
        response = await asyncio.to_thread(crawler.get, INDEX_URL)
        await writer.write(response.content, source_url=INDEX_URL, kind="html_report",
                           metadata={"endpoint": "index", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        targets = discover_reports(response.content, start_year=start_year, end_year=end_year,
                                   weeks=weeks, max_reports=None)
        selected = targets if max_reports is None else targets[:int(max_reports)]
        if max_reports is not None and not 1 <= int(max_reports) <= 260:
            raise PakistanIDSRContractError("max_reports must be between 1 and 260")
        if not selected:
            raise PakistanIDSRContractError("No official IDSR reports in requested source-year/week window")
        for report in selected:
            try:
                response = await asyncio.to_thread(crawler.get, report["pdf_url"])
                payload = response.content
                if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                    raise PakistanIDSRContractError("Incomplete or invalid NIH PDF")
            except (requests.RequestException, PakistanIDSRContractError) as exc:
                failures.append({**report, "stage": "download", "error": str(exc)})
                continue
            # Artifact persistence failures propagate instead of becoming source gaps.
            await writer.write(payload, source_url=report["pdf_url"], kind="cda_pdf",
                               metadata={"endpoint": "pdf", "report": report})
            manifest.append({"url": report["pdf_url"], "sha256": hashlib.sha256(payload).hexdigest()})
        plan = {"targets": targets, "selected": selected, "manifest": manifest,
                "failed_downloads": failures, "requested_start_year": int(start_year),
                "requested_end_year": end_year, "requested_weeks": None if weeks is None else sorted(set(map(int, weeks))),
                "max_reports": max_reports, "discovery_url": INDEX_URL,
                "omitted_by_limit": len(targets) - len(selected)}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=INDEX_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_documents(documents, *, failed_downloads=(), omitted_by_limit=0):
    rows = []
    reports = []
    for report, payload in documents:
        try:
            parsed, detail = parse_report_pdf(payload, report)
        except Exception as exc:
            reports.append({"url": report["pdf_url"], "status": "unparsed", "error": str(exc)})
            continue
        rows.extend(parsed)
        reports.append({"url": report["pdf_url"], "status": "parsed", **detail})
    selected = {}
    conflicts = set()
    for row in rows:
        identity = (row["SourcePeriodYear"], row["SourcePeriodWeek"], row["SourceDiseaseCode"],
                    row["GeographyKey"], row["DefinitionVersion"], row["Measure"])
        previous = selected.get(identity)
        if previous is not None and (previous["Cases"], previous["MissingValueStatus"]) != (row["Cases"], row["MissingValueStatus"]):
            conflicts.add(identity)
        else:
            selected[identity] = row
    rows = [row for identity, row in sorted(selected.items()) if identity not in conflicts]
    audit = {"status": "partial", "reports": reports, "failed_downloads": list(failed_downloads),
             "omitted_by_limit": omitted_by_limit, "observations": len(rows),
             "numeric_observations": sum(bool(row.get("Cases", "")) for row in rows),
             "source_not_reported_observations": sum(row.get("MissingValueStatus") == "source_not_reported" for row in rows),
             "conflicting_observations": [list(identity) for identity in sorted(conflicts)],
             "calendar_verified": False, "authoritative_replacement": False,
             "public_release_enabled": False,
             "coverage_note": "Table 1 suspected cases only; NR is unknown. Published totals are reporting-area aggregates. Missing weeks are not zero. Deaths and laboratory values are not inferred."}
    return rows, audit


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    indices = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "index"]
    plans = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "plan"]
    if len(indices) != 1 or len(plans) != 1:
        raise PakistanIDSRContractError("Exactly one captured NIH index and plan required")
    plan = json.loads(read_artifact(plans[0]))
    expected = discover_reports(read_artifact(indices[0]), start_year=plan["requested_start_year"],
        end_year=plan["requested_end_year"], weeks=plan["requested_weeks"], max_reports=None)
    selected = expected if plan["max_reports"] is None else expected[:int(plan["max_reports"])]
    if expected != plan["targets"] or selected != plan["selected"]:
        raise PakistanIDSRContractError("Captured plan differs from official discovery evidence")
    by_url = {report["pdf_url"]: report for report in selected}
    pdfs = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "pdf"]
    actual = {(artifact.source_url, artifact.sha256) for artifact in pdfs}
    expected_manifest = {(item["url"], item["sha256"]) for item in plan["manifest"]}
    if len(actual) != len(pdfs) or actual != expected_manifest:
        raise PakistanIDSRContractError("PDF artifacts do not match immutable collection manifest")
    documents = []
    for artifact in pdfs:
        if artifact.source_url not in by_url or artifact.metadata_.get("report") != by_url[artifact.source_url]:
            raise PakistanIDSRContractError("Unlinked NIH report artifact")
        documents.append((by_url[artifact.source_url], read_artifact(artifact)))
    rows, audit = organize_documents(documents, failed_downloads=plan["failed_downloads"],
                                     omitted_by_limit=plan["omitted_by_limit"])
    audit.update(discovered_reports=len(expected), selected_reports=len(selected),
                 captured_reports=len(pdfs), requested_start_year=plan["requested_start_year"],
                 requested_end_year=plan["requested_end_year"])
    return updater.prepare_rows(rows, source=source, start_year=plan["requested_start_year"], source_audit=audit)
