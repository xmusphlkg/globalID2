"""Durable NCDC archive capture followed by entirely offline organization."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.ng import HISTORY_START_YEAR, DEFAULT_SCOPE, SITREP_URL, WEEKLY_URL, NigeriaNCDCCrawler, discover_categories, discover_reports, discover_weekly_pages, parse_report
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, capture_manifest_path=None, **options):
    from src.data.processors.ng import NigeriaNCDCUpdater
    NigeriaNCDCUpdater.normalize_source(source)
    if capture_manifest_path:
        # Explicit operator recovery/offline mode; never presented as a new
        # successful fetch. Copy and register every hash-verified original.
        path = Path(capture_manifest_path).resolve()
        if not path.is_relative_to(Path("data/raw/ng").resolve()):
            raise ValueError("NCDC recovery manifest must be under data/raw/ng")
        reports = json.loads(path.read_text())
        for report in reports:
            if report.get("error"):
                continue
            payload = Path(report["path"]).read_bytes()
            if len(payload) != report["bytes"] or hashlib.sha256(payload).hexdigest() != report["sha256"]:
                raise ValueError("NCDC recovery artifact integrity failed")
            await writer.write(payload, source_url=report["url"], kind="ng_ncdc_pdf", metadata={"report": report, "recovery": True})
        await writer.write(json.dumps({"reports": reports, "mode": "explicit_offline_recovery"}).encode(), source_url=WEEKLY_URL, kind="ng_ncdc_plan")
        return writer.artifacts

    crawler = NigeriaNCDCCrawler()
    reports, failures = [], []
    try:
        for url, kind in [(WEEKLY_URL, "weekly"), (SITREP_URL, "sitrep")]:
            response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=response.url, kind="ng_ncdc_index", metadata={"kind": kind})
            pages = discover_weekly_pages(response.content) if kind == "weekly" else discover_categories(response.content)
            if kind == "weekly" and int(start_year) > HISTORY_START_YEAR:
                pages = [url for url in pages if int(re.search(r"/weekly/(\d+)/", url)[1]) >= int(start_year)]
            for page_url in pages:
                try:
                    response = await asyncio.to_thread(crawler.get, page_url)
                except Exception as exc:
                    failures.append({"url": page_url, "kind": f"{kind}_catalogue", "error": str(exc)})
                    continue
                # Persistence failures are fatal and never disguised as an
                # upstream gap or an apparently successful partial refresh.
                await writer.write(response.content, source_url=response.url, kind="ng_ncdc_listing", metadata={"kind": kind})
                try:
                    reports.extend(discover_reports(response.content, listing_url=page_url, kind=kind))
                except Exception as exc:
                    failures.append({"url": page_url, "kind": f"{kind}_catalogue", "error": str(exc)})
        reports = list({r["url"]: r for r in reports}.values())
        excluded = []
        if int(start_year) > HISTORY_START_YEAR:
            selected = []
            for report in reports:
                # Filtering catalogue report dates only; observation dates are
                # still independently verified inside the captured PDF.
                years = [int(year) for year in re.findall(r"\b20\d{2}\b", report.get("title", "") + " " + report.get("download_name", ""))]
                year = report.get("year") or (max(years) if years else None)
                if year is not None and year < int(start_year):
                    excluded.append(report)
                else:
                    selected.append(report)
            reports = selected
        for report in reports:
            try:
                response = await asyncio.to_thread(crawler.get, report["url"])
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Non-PDF NCDC response")
            except Exception as exc:
                failures.append(report | {"error": str(exc)})
                continue
            await writer.write(response.content, source_url=response.url, kind="ng_ncdc_pdf", metadata={"report": report, "retrieved_at": datetime.now(timezone.utc).isoformat()})
        await writer.write(json.dumps({"reports": reports, "failed_downloads": failures, "mode": "live", "requested_start_year": start_year, "excluded_older_reports": excluded}).encode(), source_url=WEEKLY_URL, kind="ng_ncdc_plan")
        return writer.artifacts
    finally:
        crawler.session.close()


def organize_documents(documents, *, expected_reports=None, failed_downloads=None):
    rows, audit = [], {"status": "partial", "reports": [], "failed_downloads": list(failed_downloads or []), "expected_reports": len(expected_reports or [])}
    for report, payload in documents:
        try:
            parsed, detail = parse_report(payload, report)
        except Exception as exc:
            audit["reports"].append({"url": report["url"], "kind": report["kind"], "category_id": report.get("category_id"), "status": "unparsed", "error": str(exc)})
            continue
        rows.extend(parsed)
        audit["reports"].append({"url": report["url"], "kind": report["kind"], "status": "parsed", "observations": len(parsed), **detail})
    # Source comparative tables can revise an earlier year's YTD observation.
    # Newer report periods supersede older documents; same-period conflicting
    # values are withheld and audited, never arbitrarily summed or selected.
    selected, conflicts = {}, {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["DefinitionVersion"])
        rank = (int(row["SourceReportYear"]), int(row["SourceReportWeek"]), row.get("SourceReportDate", ""))
        previous = selected.get(key)
        if previous is None or rank > previous[0]:
            selected[key] = rank, row
            conflicts.pop(key, None)
        elif rank == previous[0] and row["Cases"] != previous[1]["Cases"]:
            conflicts[key] = [previous[1]["SourceURL"], row["SourceURL"]]
    audit["conflicting_observations"] = [{"key": list(k), "urls": urls} for k, urls in conflicts.items()]
    rows = [v[1] for k, v in selected.items() if k not in conflicts]
    rows.sort(key=lambda r: (r["Date"], r["SourceDiseaseCode"], r["GeographyKey"]))
    audit["captured_reports"] = len(audit["reports"])
    audit["parsed_reports"] = sum(r["status"] == "parsed" for r in audit["reports"])
    audit["unparsed_reports"] = sum(r["status"] == "unparsed" for r in audit["reports"])
    audit["observations"] = len(rows)
    audit["coverage_note"] = "All discovered public archive categories are attempted; unverified PDF layouts, images, missing reports and intra-source conflicts remain explicit coverage gaps. Cumulative and period counts are separate."
    return rows, audit


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.kind == "ng_ncdc_plan"]
    if len(plans) != 1:
        raise ValueError("Exactly one NCDC capture plan is required")
    plan = json.loads(read_artifact(plans[0]))
    documents = ((a.metadata_["report"], read_artifact(a)) for a in artifacts if a.kind == "ng_ncdc_pdf")
    failures = list(plan.get("failed_downloads", [])) + [r for r in plan["reports"] if r.get("error")]
    rows, audit = organize_documents(documents, expected_reports=plan["reports"], failed_downloads=failures)
    audit["collection_mode"] = plan.get("mode")
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
