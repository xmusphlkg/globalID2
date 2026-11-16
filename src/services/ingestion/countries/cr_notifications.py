"""Capture the Ministry archive, then replay verified national tables offline."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from src.data.crawlers.cr_notifications import (
    BULLETINS_URL, INDIVIDUAL_URL, DEFAULT_SCOPE, HISTORY_START_YEAR,
    CostaRicaContractError, CostaRicaNotificationsCrawler, discover, parse_report_pdf,
)
from src.data.crawlers.za_cache import NICDReportCache
from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, cache_ttl_hours=24, force_refresh=False,
                  crawler_type=CostaRicaNotificationsCrawler, **options):
    CostaRicaNotificationsUpdater.normalize_source(source)
    crawler = crawler_type()
    cache = NICDReportCache(Path("data/cache/cr/moh_notifications"))
    failures, reports, seen = [], {}, set()
    pending = [INDIVIDUAL_URL, BULLETINS_URL]
    upper = int(end_year or datetime.now(timezone.utc).year)
    try:
        while pending:
            url = pending.pop(0)
            if url in seen:
                continue
            if len(seen) >= 300:
                raise CostaRicaContractError("Unexpected archive pagination depth")
            seen.add(url)
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except requests.RequestException as exc:
                failures.append({"url": url, "stage": "directory", "error": str(exc)})
                continue
            await writer.write(response.content, source_url=url, kind="html_report",
                               metadata={"endpoint": "directory", "retrieved_at": datetime.now(timezone.utc).isoformat()})
            directories, pages, discovered = discover(response.content, url, start_year=start_year, end_year=upper)
            pending.extend(u for u in list(directories) + pages if u not in seen)
            for report in discovered:
                reports.setdefault(report["url"], report)
        if not reports:
            raise CostaRicaContractError("No linked Ministry notification PDF in requested range")
        parallel = asyncio.Semaphore(4)

        async def capture_report(report):
            async with parallel:
                # Independent sessions keep cookies and transport retries out
                # of concurrent requests sharing mutable requests.Session state.
                client = crawler_type()
                try:
                    try:
                        payload, receipt = await asyncio.to_thread(cache.get, client, report["url"], ttl_hours=cache_ttl_hours,
                                                                  force_refresh=force_refresh or bool(options.get("force")))
                        if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                            raise CostaRicaContractError("Ministry file link did not return a complete PDF")
                    except (requests.RequestException, ValueError) as exc:
                        failures.append({"url": report["url"], "stage": "pdf", "error": str(exc)})
                        return
                    await writer.write(payload, source_url=report["url"], kind="cda_pdf",
                                       metadata={"endpoint": "report", "report": report, **receipt})
                finally:
                    client.session.close()

        await asyncio.gather(*(capture_report(report) for report in reports.values()))
        plan = {"reports": list(reports.values()), "failures": failures, "start_year": int(start_year),
                "end_year": upper, "directories": sorted(seen), "captured_at": datetime.now(timezone.utc).isoformat()}
        await writer.write(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode(), source_url=BULLETINS_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise CostaRicaContractError("Exactly one captured Ministry collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    captured_at = datetime.fromisoformat(plan["captured_at"].replace("Z", "+00:00"))
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise CostaRicaContractError("Captured Ministry timestamp must include a timezone")
    captured_as_of = captured_at.astimezone(ZoneInfo("America/Costa_Rica")).date()
    expected = {r["url"]: r for r in plan["reports"]}
    discovered, directories = {}, set()
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "directory":
            continue
        if artifact.source_url in directories:
            raise CostaRicaContractError("Captured Ministry directory is duplicated")
        directories.add(artifact.source_url)
        _, _, reports = discover(read_artifact(artifact), artifact.source_url,
                                 start_year=plan["start_year"], end_year=plan["end_year"])
        for report in reports:
            discovered.setdefault(report["url"], report)
    directory_failures = {r["url"] for r in plan["failures"] if r["stage"] == "directory"}
    if set(plan["directories"]) - directories != directory_failures or directories - set(plan["directories"]):
        raise CostaRicaContractError("Captured directory chain is incomplete without explicit discovery failures")
    if expected != discovered:
        raise CostaRicaContractError("Report plan does not match links in captured official directories")
    seen, rows, report_audit = set(), [], []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "report":
            continue
        report = artifact.metadata_["report"]
        url = artifact.source_url
        if url not in expected or report != expected[url] or url in seen:
            raise CostaRicaContractError("Captured report differs from plan or is duplicated")
        seen.add(url)
        payload = read_artifact(artifact)
        try:
            parsed, audit = parse_report_pdf(payload, report, as_of=captured_as_of)
        except (ValueError, RuntimeError) as exc:
            report_audit.append({"url": url, "status": "rejected", "error": str(exc)})
            continue
        for row in parsed:
            row.update(RawSHA256=artifact.sha256, DownloadURL=url)
        rows.extend(parsed)
        report_audit.append(audit)
    failed = {r["url"] for r in plan["failures"] if r["stage"] == "pdf"}
    if set(expected) - seen != failed:
        raise CostaRicaContractError("Captured PDF plan is incomplete without explicit download failures")
    # Several publications can carry the same date. Equal observations may
    # share evidence, whereas conflicting values are quarantined as a group.
    grouped = {}
    for row in rows:
        key = (row["SourceDiseaseCode"], row["Date"])
        grouped.setdefault(key, []).append(row)
    unique, conflicts = [], []
    for key, observations in grouped.items():
        if len({(r["Cases"], r.get("SourceReportedEpidemiologicalWeek"), r["SourceReportYear"]) for r in observations}) != 1:
            conflicts.append({"identity": key, "values": [r["Cases"] for r in observations], "urls": [r["SourceURL"] for r in observations]})
        else:
            unique.append(observations[0])
    audit = {"status": "partial", "coverage_complete": False, "discovered_reports": len(expected),
             "captured_reports": len(seen), "failures": plan["failures"], "reports": report_audit,
             "conflicts": conflicts, "observations": len(unique),
             "coverage_note": "Reviewed national Table 1 annual rows and dengue/chikungunya cumulative publication snapshots only; other source tables remain a documented coverage gap."}
    return updater.prepare_rows(unique, source=source, start_year=plan["start_year"], source_audit=audit)
