"""Capture Peru's complete directory; organize verified partial archive batches."""
from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event

from src.data.crawlers.pe import (DEFAULT_SCOPE, HISTORY_START_YEAR, LISTING_URL,
    LISTING_FORM, PeruCDCCrawler, PeruContractError, discover_reports, parse_bulletin_pdf)
from src.data.crawlers.pe import PARSER_SHA256
from src.data.processors.pe import PeruCDCWeeklyUpdater, PeruFetchResult
from src.services.ingestion.artifacts import read_artifact

collect_cancellation_safe = True

PARSE_CACHE_SCHEMA = "pe-cdc-physical-disease-lines-v2"


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, force=False, crawler_type=PeruCDCCrawler,
                  download_cache_directory=None, **options):
    PeruCDCWeeklyUpdater.normalize_source(source)
    crawler = (crawler_type(cache_directory=download_cache_directory)
               if download_cache_directory is not None else crawler_type())
    errors = []; captured = []; limiter = asyncio.Semaphore(8)
    stop = Event()
    workers = set()
    cooperative = bool(getattr(crawler, "supports_cooperative_cancellation", False))

    async def blocking(function, *args, **kwargs):
        if cooperative:
            kwargs["cancel_event"] = stop
        operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        workers.add(operation)
        try:
            # Cancelling collection must not detach the executor thread.
            return await asyncio.shield(operation)
        finally:
            if operation.done():
                workers.discard(operation)

    try:
        targets, listing = await blocking(crawler.discover, start_year=start_year, end_year=end_year)
        await writer.write(listing, source_url=LISTING_URL, kind="html_report",
            metadata={"endpoint": "directory", "request_method": "POST", "request_form": LISTING_FORM})
        async def download(target):
            async with limiter:
                try:
                    payload, receipt = await blocking(crawler.download, target, force=force)
                except Exception as exc:
                    errors.append({"url": target["url"], "stage": "download", "error": str(exc)})
                    return
                await writer.write(payload, source_url=target["url"], kind="cda_pdf",
                    metadata={"endpoint": "bulletin", "reference": target, "receipt": receipt})
                captured.append({"url": target["url"], "sha256": hashlib.sha256(payload).hexdigest()})
        results = await asyncio.gather(*(download(target) for target in targets), return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            raise failures[0]
        await writer.write(json.dumps({"targets": targets, "captured": captured, "errors": errors,
            "retrieved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False).encode(),
            source_url=LISTING_URL, kind="archive_json", metadata={"endpoint": "collection_audit"})
        return writer.artifacts
    finally:
        stop.set()
        # Workers flush partial bytes and close their responses cooperatively.
        # Keep cleanup owned even if a second shutdown cancellation arrives.
        cleanup = asyncio.gather(*workers, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        crawler.session.close()


def _parse(artifact, cache_directory=None):
    payload = read_artifact(artifact)
    key = hashlib.sha256(json.dumps([PARSE_CACHE_SCHEMA, artifact.sha256, artifact.source_url,
        artifact.metadata_["reference"], PARSER_SHA256], sort_keys=True).encode()).hexdigest()
    cache = Path(cache_directory) / (key + ".json") if cache_directory else None
    if cache and cache.exists():
        document = json.loads(cache.read_text())
        if document.get("schema") != PARSE_CACHE_SCHEMA or document.get("loaded_parser_sha256") != PARSER_SHA256:
            raise PeruContractError("Parse cache does not match loaded parser/schema")
        return document["result"]
    try:
        rows, report = parse_bulletin_pdf(payload, source_url=artifact.source_url,
            reference=artifact.metadata_["reference"])
        result = [rows, report, None]
    except Exception as exc:
        result = [[], None, {"url": artifact.source_url, "stage": "parse", "error": str(exc)}]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema": PARSE_CACHE_SCHEMA, "loaded_parser_sha256": PARSER_SHA256,
            "raw_sha256": artifact.sha256, "result": result}, ensure_ascii=False)); temp.replace(cache)
    return result


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
             end_year=None, parse_cache_directory=None, **options):
    updater.normalize_source(source)
    directories = [a for a in artifacts if a.metadata_.get("endpoint") == "directory"]
    audits = [a for a in artifacts if a.metadata_.get("endpoint") == "collection_audit"]
    if len(directories) != 1 or len(audits) != 1:
        raise PeruContractError("Exactly one captured official directory and collection audit required")
    expected = discover_reports(read_artifact(directories[0]), start_year=start_year, end_year=end_year)
    collection = json.loads(read_artifact(audits[0])); plan = {r["url"]: r for r in expected}
    if collection["targets"] != expected:
        raise PeruContractError("Captured archive plan differs from official directory")
    reports = [a for a in artifacts if a.kind == "cda_pdf"]
    actual = {(a.source_url, a.sha256) for a in reports}
    declared = {(r["url"], r["sha256"]) for r in collection["captured"]}
    if actual != declared or len(actual) != len(reports):
        raise PeruContractError("PDF artifacts differ from immutable batch manifest")
    for artifact in reports:
        if artifact.source_url not in plan or artifact.metadata_["reference"] != plan[artifact.source_url]:
            raise PeruContractError("Unlinked PDF or altered source directory metadata")
    rows = []; valid = []; errors = list(collection.get("errors", [])); unique = {}; conflicts = {}
    variants = {}
    for reference in expected:
        variants.setdefault((reference["year"], reference["week"]), set()).add(reference["url"])
    present = {a.source_url for a in reports}; deferred = []
    eligible = []
    for artifact in reports:
        reference = artifact.metadata_["reference"]
        siblings = variants[(reference["year"], reference["week"])]
        if not siblings.issubset(present):
            deferred.append({"url": artifact.source_url, "reason": "official_same_period_variants_pending",
                "required_variants": sorted(siblings)})
        else:
            eligible.append(artifact)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for parsed, report, error in executor.map(lambda a: _parse(a, parse_cache_directory), eligible):
            if error:
                errors.append(error)
            else:
                valid.append(report)
                for row in parsed:
                    key = (row["Date"], row["SourceDiseaseCode"], row["DefinitionVersion"])
                    if key in conflicts:
                        conflicts[key].append(row)
                    elif key in unique and unique[key]["Cases"] != row["Cases"]:
                        conflicts[key] = [unique.pop(key), row]
                    else:
                        unique[key] = row
    rows = sorted(unique.values(), key=lambda r: (r["Date"], r["SourceDiseaseCode"], r["DefinitionVersion"]))
    outstanding = [r["url"] for r in expected if r["url"] not in {a.source_url for a in reports}]
    audit = {"status": "partial" if outstanding or errors or any(r["status"] == "partial" for r in valid) else "validated",
        "published_reports": len(expected), "captured_reports": len(reports), "validated_reports": len(valid),
        "rows": len(rows), "observed_values": len(rows), "unknown_values_are_not_zero": True,
        "report_years": sorted({r["reference"]["year"] for r in valid}), "reports": valid,
        "outstanding_reports": outstanding, "failed_reports": errors,
        "deferred_variant_reports": deferred,
        "conflicting_observations": [{"key": list(key), "candidates": candidates,
            "resolution": "quarantined_without_verified_official_revision_order"}
            for key, candidates in sorted(conflicts.items())],
        "public_release_enabled": False, "license_review_status": "unverified",
        "cumulative_values_are_not_weekly_cases": True, "canonical_mappings_created": False}
    return PeruFetchResult(rows, max((date.fromisoformat(r["Date"]) for r in rows), default=None),
        updater.output_csv, [f"[PE] {len(reports)}/{len(expected)} source reports captured; {len(rows)} verified native metrics; {len(outstanding)} outstanding"], audit)
