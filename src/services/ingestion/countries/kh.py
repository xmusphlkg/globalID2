"""Capture every linked CamEWARN archive/PDF, then organize without network."""
from __future__ import annotations

import asyncio
import hashlib
import json
import requests
from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.data.crawlers.kh import (
    INDEX_URL, DEFAULT_SCOPE, HISTORY_START_YEAR, CambodiaCamEWARNCrawler,
    CambodiaContractError, archive_members, discover_reports, parse_report_pdf,
    merge_report_rows,
)
from src.data.processors.kh import CambodiaCamEWARNWeeklyUpdater, CambodiaFetchResult
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, crawler_type=CambodiaCamEWARNCrawler, **options):
    CambodiaCamEWARNWeeklyUpdater.normalize_source(source)
    crawler = crawler_type()
    errors = []; captured = []; member_manifest = []; limiter = asyncio.Semaphore(4)
    try:
        response = await asyncio.to_thread(crawler.get, INDEX_URL)
        await writer.write(response.content, source_url=INDEX_URL, kind="html_report",
            metadata={"endpoint": "index", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        targets = discover_reports(response.content, start_year=start_year, end_year=end_year)

        async def download(target):
            async with limiter:
                try:
                    response = await asyncio.to_thread(crawler.get, target["url"])
                except requests.RequestException as exc:
                    errors.append({**target, "stage": "download", "error": str(exc)})
                    return
                # Persistence errors intentionally propagate; they must never
                # masquerade as harmless unavailable source reports.
                await writer.write(response.content, source_url=target["url"],
                    kind="cda_pdf" if target["format"] == "pdf" else "kh_report_archive",
                    metadata={"endpoint": "published_file", **target,
                              "retrieval_mode": response.headers.get("X-GIDS-Retrieval-Mode", "live")})
                captured.append(target["url"])
                if target["format"] == "pdf":
                    member_manifest.append({"url": target["url"], "member": "", "year": target["year"],
                                            "sha256": hashlib.sha256(response.content).hexdigest()})
                    return
                try:
                    members = await asyncio.to_thread(archive_members, response.content, kind=target["format"])
                except Exception as exc:
                    errors.append({**target, "stage": "archive_decode", "error": str(exc)})
                    return
                for name, payload in members:
                    await writer.write(payload, source_url=target["url"], kind="cda_pdf",
                        metadata={"endpoint": "archive_member", "member": name, "year": target["year"],
                                  "archive_url": target["url"]})
                    member_manifest.append({"url": target["url"], "member": name, "year": target["year"],
                                            "sha256": hashlib.sha256(payload).hexdigest()})
        results = await asyncio.gather(*(download(target) for target in targets), return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise failures[0]
        await writer.write(json.dumps({"targets": targets, "captured": sorted(captured),
            "errors": errors, "requested_start_year": start_year, "requested_end_year": end_year,
            "member_manifest": sorted(member_manifest, key=lambda item: (item["url"], item["member"])),
            "retrieved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False).encode(),
            source_url=INDEX_URL, kind="archive_json", metadata={"endpoint": "collection_audit"})
        return writer.artifacts
    finally:
        crawler.session.close()


def _parse_one(artifact, cache_directory=None):
    payload = read_artifact(artifact)  # Integrity/persistence errors fail the run.
    cache_path = None
    if cache_directory:
        import src.data.crawlers.kh as source_parser
        parser_hash = hashlib.sha256(Path(source_parser.__file__).read_bytes()).hexdigest()
        cache_key = hashlib.sha256(json.dumps([artifact.sha256, artifact.source_url,
            artifact.metadata_, parser_hash], sort_keys=True).encode()).hexdigest()
        cache_path = Path(cache_directory) / (cache_key + ".json")
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("raw_sha256") != artifact.sha256 or cached.get("parser_hash") != parser_hash:
                raise CambodiaContractError("Derived parse cache does not match captured source/parser")
            return cached["result"]
    try:
        rows, report = parse_report_pdf(payload, source_url=artifact.source_url,
            expected_year=int(artifact.metadata_["year"]), member=artifact.metadata_.get("member", ""))
        result = rows, report, None
    except Exception as exc:
        result = [], None, {"url": artifact.source_url, "member": artifact.metadata_.get("member", ""),
                          "year": artifact.metadata_.get("year"), "stage": "parse", "error": str(exc)}
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"raw_sha256": artifact.sha256, "parser_hash": parser_hash,
                                        "result": result}, ensure_ascii=False))
        temporary.replace(cache_path)
    return result


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
             end_year=None, parse_cache_directory=None, **options):
    updater.normalize_source(source)
    indices = [a for a in artifacts if a.metadata_.get("endpoint") == "index"]
    audits = [a for a in artifacts if a.metadata_.get("endpoint") == "collection_audit"]
    if len(indices) != 1 or len(audits) != 1:
        raise CambodiaContractError("One captured official index and collection audit required")
    expected = discover_reports(read_artifact(indices[0]), start_year=start_year, end_year=end_year)
    collection = json.loads(read_artifact(audits[0]))
    if collection["targets"] != expected:
        raise CambodiaContractError("Captured discovery plan differs from requested official index")
    allowed = {target["url"] for target in expected}
    reports = [a for a in artifacts if a.kind == "cda_pdf"]
    if any(a.source_url not in allowed for a in reports):
        raise CambodiaContractError("Unlinked CamEWARN PDF artifact")
    expected_members = {(item["url"], item["member"], int(item["year"]), item["sha256"])
                        for item in collection["member_manifest"]}
    actual_members = {(a.source_url, a.metadata_.get("member", ""), int(a.metadata_["year"]), a.sha256)
                      for a in reports}
    if expected_members != actual_members or len(actual_members) != len(reports):
        raise CambodiaContractError("Captured PDF members do not match immutable collection manifest")
    # pdfplumber documents are independent per worker. This is pure captured
    # source parsing; no source access or store writes occur here.
    rows_by_report = []; valid = []; errors = list(collection["errors"])
    with ThreadPoolExecutor(max_workers=4) as executor:
        for rows, report, error in executor.map(lambda artifact: _parse_one(artifact, parse_cache_directory), reports):
            if error:
                errors.append(error)
            else:
                rows_by_report.append(rows); valid.append(report)
    rows = merge_report_rows(rows_by_report)
    if not rows:
        raise CambodiaContractError("No independently validated CamEWARN categories: " + json.dumps(errors))
    chart_only = sum(json.loads(row["Dimensions"])["chart_only"] for row in rows)
    audit = {
        "status": "partial" if errors or chart_only else "validated",
        "published_files": len(expected), "captured_files": len(collection["captured"]),
        "pdf_artifacts": len(reports), "validated_reports": len(valid),
        "rows": len(rows), "observed_values": sum(row["Cases"] != "" for row in rows),
        "chart_only_rows": chart_only,
        "categories": sorted({row["SourceDiseaseCode"] for row in rows}),
        "published_years": sorted({target["year"] for target in expected}),
        "report_years": sorted({report["report_year"] for report in valid}),
        "failed_reports": errors, "reports": valid,
        "public_release_enabled": False, "license_review_status": "unverified",
        "authoritative_replacement": False,
        "unpublished_weeks_are_not_zero": True,
        "cases_are_not_reporting_completeness_counts": True,
    }
    return CambodiaFetchResult(rows, max(date.fromisoformat(row["Date"]) for row in rows), updater.output_csv,
        [f"[KH] {len(valid)}/{len(reports)} reports validated; {len(rows)} source-native category/week rows; "
         f"{audit['observed_values']} exact values; {chart_only} chart-only unknowns"], audit)
