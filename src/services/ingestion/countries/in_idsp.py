"""Capture official IDSP archives/PDFs, then organize without network access."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.data.crawlers.in_idsp import (
    DEFAULT_SCOPE, DIRECTORY_URL, HISTORY_START_YEAR, IndiaIDSPContractError,
    IndiaIDSPCrawler, discover_reports, parse_report_pdf,
)
from src.data.crawlers.za_cache import NICDReportCache
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, end_year=None,
                  max_reports=8, cache_ttl_hours=24, force_refresh=False,
                  crawler_type=IndiaIDSPCrawler, **options):
    from src.data.processors.in_idsp import IndiaIDSPUpdater
    IndiaIDSPUpdater.normalize_source(source)
    limit = int(max_reports)
    if not 1 <= limit <= 1000:
        raise ValueError("IDSP max_reports must be between 1 and 1000")
    crawler = crawler_type()
    cache = NICDReportCache(directory=Path("data/cache/in/idsp_outbreak_reports"))
    failures = []
    try:
        response = await asyncio.to_thread(crawler.get, DIRECTORY_URL)
        if urlparse(response.url).hostname != "ncdc.mohfw.gov.in":
            raise IndiaIDSPContractError("Official archive redirected outside NCDC")
        await writer.write(response.content, source_url=response.url, kind="html_report",
                           metadata={"endpoint": "directory", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        available = discover_reports(response.content, start_year=start_year, end_year=end_year)
        reports = available[-limit:]
        download_workers = max(1, min(int(options.get("download_workers", 4)), 8))
        limiter = asyncio.Semaphore(download_workers)

        async def download(report):
            async with limiter:
                worker_crawler = crawler_type()
                try:
                    payload, receipt = await asyncio.to_thread(
                        cache.get, worker_crawler, report["pdf_url"], ttl_hours=cache_ttl_hours,
                        force_refresh=force_refresh or bool(options.get("force")),
                    )
                    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
                        raise IndiaIDSPContractError("Incomplete IDSP PDF response")
                    return report, payload, receipt, None
                except (requests.RequestException, IndiaIDSPContractError) as exc:
                    return report, None, None, {**report, "stage": "pdf", "error": str(exc)}
                finally:
                    worker_crawler.session.close()

        # Downloads overlap, while artifact writes remain in archive order so
        # manifests and replay receipts stay deterministic.
        downloaded = await asyncio.gather(*(download(report) for report in reports))
        for report, payload, receipt, error in downloaded:
            if error:
                failures.append(error)
                continue
            await writer.write(payload, source_url=report["pdf_url"], kind="cda_pdf",
                               metadata={"endpoint": "pdf", "report": report,
                                         "retrieved_at": receipt["retrieved_at"],
                                         "revalidated_at": receipt["revalidated_at"],
                                         "cache_status": receipt["cache_status"]})
        plan = {"reports": reports, "available_reports": len(available),
                "omitted_by_limit": len(available) - len(reports), "max_reports": limit,
                "download_workers": download_workers,
                "requested_start_year": int(start_year), "requested_end_year": end_year,
                "failed_downloads": failures, "discovery_url": DIRECTORY_URL}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=DIRECTORY_URL,
                           kind="archive_json", metadata={"endpoint": "plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


PARSER_CACHE_SCHEMA = 1


def _parser_hash():
    # Hash the canonical crawler module rather than the imported function
    # object. This remains stable when callers instrument the connector in
    # tests and invalidates the cache whenever parser code changes.
    import src.data.crawlers.in_idsp as parser_module
    return hashlib.sha256(Path(parser_module.__file__).read_bytes()).hexdigest()


def _parse_document(item, cache_directory=None):
    if len(item) == 2:
        report, payload = item
        raw_sha256 = hashlib.sha256(payload).hexdigest()
    else:
        report, payload, raw_sha256 = item
    cache_path = None
    parser_hash = _parser_hash()
    if cache_directory:
        cache_key = hashlib.sha256(json.dumps(
            [raw_sha256, report, parser_hash, PARSER_CACHE_SCHEMA], sort_keys=True,
        ).encode()).hexdigest()
        cache_path = Path(cache_directory) / f"{cache_key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if (cached.get("schema") != PARSER_CACHE_SCHEMA
                    or cached.get("raw_sha256") != raw_sha256
                    or cached.get("parser_hash") != parser_hash):
                raise IndiaIDSPContractError("IDSP derived parse cache does not match source/parser")
            report, parsed, detail = cached["result"]
            detail["parse_cache"] = "hit"
            return report, parsed, detail
    try:
        parsed, detail = parse_report_pdf(payload, report)
    except IndiaIDSPContractError as exc:
        result = report, [], {"url": report["pdf_url"], "status": "unparsed", "error": str(exc)}
    else:
        result = report, parsed, {"url": report["pdf_url"], "status": "parsed", **detail}
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.with_suffix(".tmp").write_text(json.dumps({
            "schema": PARSER_CACHE_SCHEMA, "raw_sha256": raw_sha256,
            "parser_hash": parser_hash, "result": result,
        }, ensure_ascii=False))
        cache_path.with_suffix(".tmp").replace(cache_path)
        result[2]["parse_cache"] = "miss"
    return result


def organize_documents(documents, *, expected_reports, failed_downloads=(), parse_workers=1,
                       parse_cache_directory=None):
    source_urls = [item[0]["pdf_url"] for item in documents]
    if len(source_urls) != len(set(source_urls)):
        raise IndiaIDSPContractError("Duplicate IDSP PDF artifact in immutable replay")
    rows, details, selected, conflicts = [], [], {}, set()
    worker_count = max(1, min(int(parse_workers or 1), 32))
    if worker_count > 1 and len(documents) > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            parsed_documents = list(pool.map(
                lambda item: _parse_document(item, parse_cache_directory), documents,
            ))
    else:
        parsed_documents = [_parse_document(item, parse_cache_directory) for item in documents]
    for report, parsed, detail in parsed_documents:
        details.append(detail)
        for row in parsed:
            key = (row["Date"], row["SourceDiseaseCode"], row["GeographyKey"], row["Dimensions"])
            if key in selected and selected[key]["Cases"] != row["Cases"]:
                conflicts.add(key)
            selected.setdefault(key, row)
    rows = [row for key, row in selected.items() if key not in conflicts]
    audit = {"status": "partial", "reports": details, "expected_reports": len(expected_reports),
             "captured_reports": len(documents), "failed_downloads": list(failed_downloads),
             "parsed_reports": sum(item["status"] in {"parsed", "parsed_cache"} for item in details),
             "parse_cache_hits": sum(item.get("parse_cache") == "hit" for item in details),
             "parse_cache_misses": sum(item.get("parse_cache") == "miss" for item in details),
             "conflicting_observations": [list(key) for key in sorted(conflicts)],
             "observations": len(rows), "coverage_note": "Outbreak event snapshots only; missing reports and NIL reporting are not zero cases. No authoritative window replacement."}
    return sorted(rows, key=lambda row: (row["Date"], row["GeographyKey"], row["Dimensions"], row["Measure"])), audit


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [artifact for artifact in artifacts if artifact.metadata_.get("endpoint") == "plan"]
    if len(plans) != 1:
        raise IndiaIDSPContractError("Exactly one IDSP collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    expected = {report["pdf_url"]: report for report in plan["reports"]}
    documents = []
    for artifact in artifacts:
        if artifact.metadata_.get("endpoint") != "pdf":
            continue
        report = artifact.metadata_["report"]
        if artifact.source_url not in expected or report != expected[artifact.source_url]:
            raise IndiaIDSPContractError("Unlinked or changed IDSP PDF artifact")
        payload = read_artifact(artifact)
        documents.append((report, payload,
                          getattr(artifact, "sha256", hashlib.sha256(payload).hexdigest())))
    rows, audit = organize_documents(documents, expected_reports=plan["reports"],
                                      failed_downloads=plan["failed_downloads"],
                                      parse_workers=options.get("parse_workers", 1),
                                      parse_cache_directory=options.get("parse_cache_directory", "data/cache/in/idsp-parses"))
    audit.update({key: plan[key] for key in ("available_reports", "omitted_by_limit", "max_reports", "requested_start_year", "requested_end_year")})
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
