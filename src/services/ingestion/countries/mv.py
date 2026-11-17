"""Capture Maldives official directories and attachments before offline parsing."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import requests

from src.data.crawlers.mv import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, INDEX_URL, HPA_URL,
    MaldivesContractError, MaldivesHPACrawler, discover_reports, discover_pdfs, parse_report_pdf,
)
from src.data.processors.mv import MaldivesHPAUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, max_reports=100, max_pages=30,
                  crawler_type=MaldivesHPACrawler, **options):
    MaldivesHPAUpdater.normalize_source(source)
    if not 1 <= int(max_reports) <= 2000 or not 1 <= int(max_pages) <= 100:
        raise ValueError("Maldives report/page bounds are invalid")
    crawler = crawler_type()
    pending, seen, reports, failures = [INDEX_URL], set(), {}, []
    upper = int(end_year or datetime.now(timezone.utc).year)
    try:
        while pending:
            url = pending.pop(0)
            if url in seen:
                continue
            if len(seen) >= int(max_pages):
                raise MaldivesContractError("Maldives directory exceeded explicit page limit")
            seen.add(url)
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except requests.RequestException as exc:
                failures.append({"url": url, "stage": "directory", "error": str(exc)})
                continue
            await writer.write(response.content, source_url=url, kind="html_report", metadata={"endpoint": "directory"})
            refs, pages = discover_reports(response.content, url, start_year=start_year, end_year=upper)
            pending.extend(u for u in pages if u not in seen)
            reports.update({r["url"]: r for r in refs})
        selected = sorted(reports.values(), key=lambda r: (r["year"], r["month"], r["url"]), reverse=True)[:int(max_reports)]
        captured = []
        for ref in selected:
            try:
                response = await asyncio.to_thread(crawler.get, ref["url"])
                await writer.write(response.content, source_url=ref["url"], kind="html_report", metadata={"endpoint": "detail", "report": ref})
                report = discover_pdfs(response.content, ref)
            except (requests.RequestException, ValueError) as exc:
                failures.append({"url": ref["url"], "stage": "detail", "error": str(exc)})
                continue
            try:
                response = await asyncio.to_thread(crawler.get, report["pdf_url"])
                if not response.content.startswith(b"%PDF"):
                    raise MaldivesContractError("HPA attachment returned non-PDF bytes")
                artifact = await writer.write(response.content, source_url=report["pdf_url"], kind="mv_hpa_pdf", metadata={"endpoint": "report", "report": report})
                captured.append({"report": report, "sha256": artifact.sha256})
            except (requests.RequestException, ValueError) as exc:
                failures.append({"url": report["pdf_url"], "stage": "pdf", "error": str(exc)})
        plan = {"source": source, "start_year": int(start_year), "end_year": upper,
                "directories": sorted(seen), "reports": list(reports.values()), "selected": selected,
                "captured": captured, "failures": failures, "max_reports": int(max_reports),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "unverified_channels": {"dengue_weekly": "no_public_report_discovered", "sidas": "not_a_verified_public_api"}}
        await writer.write(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode(), source_url=INDEX_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise MaldivesContractError("Exactly one frozen Maldives collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    dirs = [a for a in artifacts if a.metadata_.get("endpoint") == "directory"]
    directory_map = {a.source_url: a for a in dirs}
    if len(dirs) != len(directory_map):
        raise MaldivesContractError("Duplicate Maldives directory artifact")
    failed_dirs = {f["url"] for f in plan["failures"] if f["stage"] == "directory"}
    if set(plan["directories"]) != set(directory_map) | failed_dirs or set(directory_map) & failed_dirs:
        raise MaldivesContractError("Captured directory plan is incomplete")
    discovered, linked_pages = {}, {INDEX_URL}
    for url, artifact in directory_map.items():
        refs, pages = discover_reports(read_artifact(artifact), url, start_year=plan["start_year"], end_year=plan["end_year"])
        linked_pages.update(pages)
        discovered.update({r["url"]: r for r in refs})
    if set(plan["directories"]) != linked_pages or discovered != {r["url"]: r for r in plan["reports"]}:
        raise MaldivesContractError("Report plan differs from captured official directory links")
    selected = sorted(discovered.values(), key=lambda r: (r["year"], r["month"], r["url"]), reverse=True)[:plan["max_reports"]]
    if selected != plan["selected"]:
        raise MaldivesContractError("Selected Maldives reports differ from frozen discovery")
    details = [a for a in artifacts if a.metadata_.get("endpoint") == "detail"]
    detail_map = {a.source_url: a for a in details}
    if len(details) != len(detail_map):
        raise MaldivesContractError("Duplicate Maldives report detail")
    expected = {}
    for ref in selected:
        artifact = detail_map.get(ref["url"])
        if artifact is None:
            if not any(f["stage"] == "detail" and f["url"] == ref["url"] for f in plan["failures"]):
                raise MaldivesContractError("Missing report detail without explicit failure")
            continue
        try:
            report = discover_pdfs(read_artifact(artifact), ref)
        except MaldivesContractError:
            if not any(f["stage"] == "detail" and f["url"] == ref["url"] for f in plan["failures"]):
                raise
            continue
        expected[report["pdf_url"]] = report
    pdfs = [a for a in artifacts if a.metadata_.get("endpoint") == "report"]
    frozen = {x["report"]["pdf_url"]: x for x in plan["captured"]}
    if len(pdfs) != len(frozen) or {a.source_url: a.sha256 for a in pdfs} != {u: x["sha256"] for u, x in frozen.items()}:
        raise MaldivesContractError("PDF artifacts differ from frozen collection manifest")
    failed_pdfs = {f["url"] for f in plan["failures"] if f["stage"] == "pdf"}
    if set(expected) != set(frozen) | failed_pdfs or set(frozen) & failed_pdfs:
        raise MaldivesContractError("Missing PDF requires explicit download failure")
    rows, report_audits = [], []
    for artifact in pdfs:
        report = expected.get(artifact.source_url)
        if report is None or report != artifact.metadata_["report"] or report != frozen[artifact.source_url]["report"]:
            raise MaldivesContractError("PDF attachment was not linked from captured official detail")
        try:
            parsed, audit = parse_report_pdf(read_artifact(artifact), report)
            rows.extend(parsed)
            report_audits.append({"url": artifact.source_url, "status": "parsed", **audit})
        except ValueError as exc:
            report_audits.append({"url": artifact.source_url, "status": "unparsed", "error": str(exc)})
    grouped = {}
    for row in rows:
        grouped.setdefault((row["Date"], row["SourceDiseaseCode"], row["GeographyKey"]), []).append(row)
    unique, conflicts = [], []
    for key, values in grouped.items():
        if len({r["Cases"] for r in values}) != 1:
            conflicts.append({"identity": key, "values": [r["Cases"] for r in values]})
        else:
            unique.append(values[0])
    audit = {"status": "partial" if unique else "source_access_blocked" if not directory_map else "documents_only",
             "coverage_complete": False, "discovered_reports": len(discovered), "captured_reports": len(pdfs),
             "deferred_reports": len(discovered) - len(selected), "failures": plan["failures"],
             "reports": report_audits, "conflicts": conflicts, "unverified_channels": plan["unverified_channels"],
             "latest_report_period": max((f"{r['year']:04d}-{r['month']:02d}" for r in discovered.values()), default=None),
             "coverage_note": "Source labels only; Atoll incidence, unlabeled curves and public weekly SIDAS feed remain unavailable."}
    return updater.prepare_rows(unique, source=source, start_year=plan["start_year"], source_audit=audit)
