"""Capture official RCDC publications before offline extraction and audit."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json

import requests

from src.data.crawlers.bt import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, LANDING_URL, PUBLICATIONS_URL, BhutanRCDCContractError,
    BhutanRCDCCrawler, discover_publications, discover_pdfs, discover_events, parse_report_pdf,
)
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, max_reports=24, listing_url=None,
                  crawler_type=BhutanRCDCCrawler, **options):
    from src.data.processors.bt import BhutanRCDCPublicationsUpdater
    BhutanRCDCPublicationsUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 2000:
        raise ValueError("RCDC max_reports must be between 1 and 2000")
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    listing_url = listing_url or LANDING_URL
    if listing_url not in {LANDING_URL, PUBLICATIONS_URL}:
        raise ValueError("RCDC listing_url must be the official homepage or News and Publications archive")
    try:
        response = await asyncio.to_thread(crawler.get, listing_url)
        await writer.write(response.content, source_url=listing_url, kind="html_report",
                           metadata={"endpoint": "listing", "retrieved_at": retrieved})
        publications = discover_publications(response.content, start_year=start_year, end_year=end_year)
        selected = publications[:int(max_reports)]
        reports, failures, captured_publications = [], [], []
        for publication in selected:
            try:
                page = await asyncio.to_thread(crawler.get, publication["publication_url"])
                await writer.write(page.content, source_url=publication["publication_url"], kind="html_report",
                                   metadata={"endpoint": "publication", "report": publication, "retrieved_at": retrieved})
                urls = discover_pdfs(page.content, publication)
            except (requests.RequestException, BhutanRCDCContractError) as exc:
                failures.append({**publication, "stage": "publication", "error": str(exc)})
                continue
            captured_publications.append({**publication, "pdf_urls": urls})
            for url in urls:
                try:
                    document = await asyncio.to_thread(crawler.get, url)
                    if not document.content.startswith(b"%PDF") or b"%%EOF" not in document.content[-65536:]:
                        raise BhutanRCDCContractError("Invalid/incomplete RCDC attachment")
                except (requests.RequestException, BhutanRCDCContractError) as exc:
                    failures.append({**publication, "pdf_url": url, "stage": "pdf", "error": str(exc)})
                    continue
                report = {**publication, "pdf_url": url, "sha256": hashlib.sha256(document.content).hexdigest()}
                # One attachment can be linked by two publication posts. Keep
                # a single immutable artifact and both publication receipts.
                if not any(item["pdf_url"] == url for item in reports):
                    await writer.write(document.content, source_url=url, kind="cda_pdf",
                                       metadata={"endpoint": "pdf", "report": report, "retrieved_at": retrieved})
                reports.append(report)
        plan = {"country_code": "BT", "source": DEFAULT_SCOPE, "retrieved_at": retrieved,
                "listing_url": listing_url, "listing_sha256": hashlib.sha256(response.content).hexdigest(),
                "start_year": int(start_year), "end_year": end_year, "max_reports": int(max_reports),
                "discovered_publications": publications, "selected_publications": selected,
                "latest_event_reports": discover_events(response.content, start_year=start_year, end_year=end_year),
                "captured_publications": captured_publications, "reports": reports, "failures": failures,
                "deferred_publications": len(publications) - len(selected)}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=listing_url,
                           kind="archive_json", metadata={"endpoint": "plan", "retrieved_at": retrieved})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    artifacts = list(artifacts)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    listings = [a for a in artifacts if a.metadata_.get("endpoint") == "listing"]
    if len(plans) != 1 or len(listings) != 1:
        raise BhutanRCDCContractError("RCDC replay requires one listing and one plan")
    plan = json.loads(read_artifact(plans[0]))
    listing_bytes = read_artifact(listings[0])
    if (plan["country_code"] != updater.country_code or plan["source"] != DEFAULT_SCOPE
            or plan.get("listing_url", LANDING_URL) != listings[0].source_url
            or hashlib.sha256(listing_bytes).hexdigest() != plan["listing_sha256"]
            or discover_publications(listing_bytes, start_year=plan["start_year"], end_year=plan["end_year"]) != plan["discovered_publications"]
            or discover_events(listing_bytes, start_year=plan["start_year"], end_year=plan["end_year"]) != plan["latest_event_reports"]
            or plan["selected_publications"] != plan["discovered_publications"][:plan["max_reports"]]):
        raise BhutanRCDCContractError("RCDC plan differs from captured official listing")
    pages = {a.source_url: a for a in artifacts if a.metadata_.get("endpoint") == "publication"}
    pdfs = {a.source_url: a for a in artifacts if a.metadata_.get("endpoint") == "pdf"}
    if len(pages) != sum(a.metadata_.get("endpoint") == "publication" for a in artifacts) or len(pdfs) != sum(a.metadata_.get("endpoint") == "pdf" for a in artifacts):
        raise BhutanRCDCContractError("Duplicate RCDC source artifacts")
    selected = {p["publication_url"]: p for p in plan["selected_publications"]}
    for publication in plan["captured_publications"]:
        url = publication["publication_url"]
        if url not in selected or any(publication.get(k) != v for k, v in selected[url].items()) or url not in pages:
            raise BhutanRCDCContractError("Unlinked RCDC publication artifact")
        if discover_pdfs(read_artifact(pages[url]), selected[url]) != publication["pdf_urls"]:
            raise BhutanRCDCContractError("RCDC captured PDF links differ from plan")
    if set(pdfs) != {report["pdf_url"] for report in plan["reports"]}:
        raise BhutanRCDCContractError("RCDC PDF artifact set differs from plan")
    captured = {p["publication_url"]: p for p in plan["captured_publications"]}
    rows, details, conflicts, values = [], [], set(), {}
    for report in plan["reports"]:
        publication = captured.get(report["publication_url"])
        if (publication is None or report["pdf_url"] not in publication["pdf_urls"]
                or any(report.get(key) != value for key, value in selected[report["publication_url"]].items())):
            raise BhutanRCDCContractError("RCDC document lacks official publication link")
        payload = read_artifact(pdfs[report["pdf_url"]])
        if hashlib.sha256(payload).hexdigest() != report["sha256"]:
            raise BhutanRCDCContractError("RCDC document hash differs from collection receipt")
        if report["publication_kind"] != "respiratory_surveillance":
            details.append({**report, "status": "document_only", "reason": "Monthly, quarterly, annual and event documents retained; no inferred national weekly cases"})
            continue
        try:
            parsed, audit = parse_report_pdf(payload, report)
        except BhutanRCDCContractError as exc:
            details.append({**report, "status": "unparsed", "error": str(exc)})
            continue
        details.append({**report, "status": "parsed", **audit})
        for row in parsed:
            key = row["Date"], row["SourceDiseaseCode"]
            previous = values.get(key)
            if previous and (previous["Cases"], previous["SourceSamplesTested"]) != (row["Cases"], row["SourceSamplesTested"]):
                conflicts.add(key)
            else:
                values[key] = row
    rows = sorted((row for key, row in values.items() if key not in conflicts), key=lambda row: row["Date"])
    audit = {"status": "partial", "coverage_complete": False, "discovery_url": LANDING_URL,
             "discovered_publications": len(plan["discovered_publications"]),
             "captured_publications": plan["captured_publications"], "deferred_publications": plan["deferred_publications"],
             "reports": details, "failed_downloads": plan["failures"],
             "events": [p for p in plan["captured_publications"] if p["publication_kind"] == "event_report"],
             "latest_event_reports": plan["latest_event_reports"],
             "conflicts": [list(key) for key in sorted(conflicts)],
             "coverage_note": "Publication discovery is broader than numerical extraction. Only explicit weekly influenza laboratory numerator/denominator fractions are parsed. Missing dates, image-only values, biweekly totals and outbreak narratives are not weekly cases.",
             "refresh_policy": "Rediscover dated official posts and refetch linked originals; increase max_reports for historical capture."}
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
