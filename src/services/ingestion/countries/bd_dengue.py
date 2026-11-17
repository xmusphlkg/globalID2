"""Capture the official DGHS directory and originals before pure PDF parsing."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json

from src.data.crawlers.bd_dengue import DEFAULT_SCOPE, BangladeshDGHSDengueCrawler, directory_url, discover_reports, parse_report
from src.data.processors.bd_dengue import BangladeshDGHSDengueUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, crawler_type=BangladeshDGHSDengueCrawler, max_reports=100, max_pages=10, start_year=None, months=None, **options):
    BangladeshDGHSDengueUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 1000 or not 1 <= int(max_pages) <= 30:
        raise ValueError("DGHS report/page bound outside reviewed range")
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    plan = {"retrieved_at": retrieved, "listings": [], "selected_reports": [], "captured_reports": [], "failed_downloads": [], "coverage_complete": False}
    try:
        discovered = {}
        for page in range(1, int(max_pages) + 1):
            url = directory_url(page)
            listing = await asyncio.to_thread(crawler.get, url)
            await writer.write(listing.content, source_url=url, kind="html_report", metadata={"endpoint": "bd_dghs_listing", "page": page, "retrieved_at": retrieved})
            reports, last_page = discover_reports(listing.content, base_url=url)
            plan["listings"].append({"url": url, "sha256": hashlib.sha256(listing.content).hexdigest()})
            for report in reports:
                if report["url"] in discovered:
                    raise ValueError("DGHS pagination repeated a publication")
                discovered[report["url"]] = report
            if page >= last_page or len(discovered) >= int(max_reports):
                break
        selected = [r for r in discovered.values() if (start_year is None or int(r["date"][:4]) >= int(start_year)) and (not months or (int(r["date"][:4]), int(r["date"][5:7])) in months)]
        selected.sort(key=lambda r: (r["date"], r["url"]), reverse=True)
        plan["discovered_reports"] = len(discovered)
        plan["selected_reports"] = selected[:int(max_reports)]
        for report in plan["selected_reports"]:
            try:
                response = await asyncio.to_thread(crawler.get, report["url"])
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("DGHS publication is not PDF")
            except Exception as exc:
                plan["failed_downloads"].append({**report, "error": str(exc)})
                continue
            # Persistence failures must fail the collection, not masquerade
            # as upstream download failures while silently losing originals.
            await writer.write(response.content, source_url=report["url"], kind="cda_pdf", metadata={"endpoint": "bd_dghs_pdf", "report_date": report["date"], "retrieved_at": retrieved})
            plan["captured_reports"].append({**report, "sha256": hashlib.sha256(response.content).hexdigest()})
        if not plan["captured_reports"]:
            raise ValueError("No DGHS daily PDFs captured in requested window")
        await writer.write(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode(), source_url=directory_url(), kind="archive_json", metadata={"endpoint": "bd_dghs_plan", "retrieved_at": retrieved})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    artifacts = list(artifacts)
    if any(a.metadata_.get("endpoint") not in {"bd_dghs_plan", "bd_dghs_listing", "bd_dghs_pdf"} for a in artifacts):
        raise ValueError("Unexpected DGHS capture artifact")
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "bd_dghs_plan"]
    if len(plans) != 1:
        raise ValueError("Exactly one DGHS collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    listings = {a.source_url: a for a in artifacts if a.metadata_.get("endpoint") == "bd_dghs_listing"}
    pdfs = {a.source_url: a for a in artifacts if a.metadata_.get("endpoint") == "bd_dghs_pdf"}
    if len(artifacts) != 1 + len(listings) + len(pdfs) or len(listings) != len(plan["listings"]) or len(pdfs) != len(plan["captured_reports"]):
        raise ValueError("DGHS artifact count differs from plan")
    discovered = {}
    for listing in plan["listings"]:
        artifact = listings[listing["url"]]
        if artifact.sha256 != listing["sha256"]:
            raise ValueError("DGHS directory hash differs from plan")
        found, _ = discover_reports(read_artifact(artifact), base_url=artifact.source_url)
        for report in found:
            if report["url"] in discovered:
                raise ValueError("DGHS duplicate directory publication")
            discovered[report["url"]] = report
    selected = plan["selected_reports"]
    covered = plan["captured_reports"] + plan["failed_downloads"]
    if len(covered) != len(selected) or {r["url"] for r in covered} != {r["url"] for r in selected}:
        raise ValueError("DGHS plan omitted or duplicated selected report")
    rows, audits, failures, seen = [], [], [], set()
    for report in plan["captured_reports"]:
        artifact = pdfs[report["url"]]
        if artifact.sha256 != report["sha256"] or discovered.get(report["url"]) != {k: report[k] for k in ("title", "date", "publication_date", "url")}:
            raise ValueError("DGHS PDF differs from captured official directory")
        payload = read_artifact(artifact)
        try:
            parsed, audit = parse_report(payload, report_date=report["date"], as_of=datetime.fromisoformat(plan["retrieved_at"]).date(), provenance={"DownloadURL": artifact.source_url, "RetrievedAt": plan["retrieved_at"], "SourcePublishedAt": report["publication_date"]})
        except ValueError as exc:
            failures.append({"url": report["url"], "error": str(exc)})
            continue
        for row in parsed:
            identity = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"])
            if identity in seen:
                raise ValueError("DGHS conflicting same-day publications require review")
            seen.add(identity)
            rows.append(row)
        audits.append(audit)
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update({**plan, "parsed_reports": audits, "parse_failures": failures, "source_observations": len(result.rows), "coverage_note": "Bounded currently published DGHS press releases; pre-September-2026 archive and dashboard are unverified. Missing days remain unknown; hospital admissions are not general case incidence."})
    return result
