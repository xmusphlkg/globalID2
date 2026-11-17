"""Durable official CKAN/CSV/XLSX capture and strictly offline HIV organization."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json

from src.core.ar_hiv_source import CATALOGUE_URL
from src.data.crawlers.ar_hiv import (
    ArgentinaHIVAnnualCrawler, ArgentinaHIVContractError, DEFAULT_SCOPE,
    INTERMEDIATE_PATH, INTERMEDIATE_SHA256, INTERMEDIATE_URL,
    discover_resources, parse_hiv_payload,
)
from src.data.processors.ar_hiv import ArgentinaHIVAnnualUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, crawler_type=ArgentinaHIVAnnualCrawler, **options):
    ArgentinaHIVAnnualUpdater.normalize_source(source)
    crawler = crawler_type()
    try:
        response = await asyncio.to_thread(crawler.get, CATALOGUE_URL)
        retrieved = datetime.now(timezone.utc).isoformat()
        await writer.write(response.content, source_url=CATALOGUE_URL, kind="archive_json",
                           metadata={"endpoint": "ar_hiv_catalogue", "retrieved_at": retrieved})
        package, resources = discover_resources(response.content)
        captures = {}
        for kind in ("csv", "xlsx"):
            resource = resources[kind]
            response = await asyncio.to_thread(crawler.get_health_resource, resource["url"])
            metadata = {"endpoint": f"ar_hiv_{kind}", "resource_id": resource["id"],
                        "resource_last_modified": resource["last_modified"],
                        "catalog_last_modified": package.get("metadata_modified"),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "tls_verification": "existing_ca_roots_plus_pinned_missing_intermediate",
                        "intermediate_sha256": INTERMEDIATE_SHA256}
            await writer.write(response.content, source_url=resource["url"],
                               kind="historical_csv" if kind == "csv" else "workbook_xlsx", metadata=metadata)
            captures[kind] = {"url": resource["url"], "sha256": hashlib.sha256(response.content).hexdigest(),
                              "byte_size": len(response.content)}
        intermediate = INTERMEDIATE_PATH.read_bytes()
        if hashlib.sha256(intermediate).hexdigest() != INTERMEDIATE_SHA256:
            raise ArgentinaHIVContractError("Transport intermediate certificate integrity failed")
        await writer.write(intermediate, source_url=INTERMEDIATE_URL, kind="tls_intermediate",
                           metadata={"endpoint": "ar_hiv_tls_intermediate", "role": "missing_chain_certificate_not_new_root"})
        plan = {"package_id": package["id"], "resources": resources,
                "catalog_sha256": next(a.sha256 for a in writer.artifacts if a.metadata_.get("endpoint") == "ar_hiv_catalogue"),
                "captures": captures, "intermediate_sha256": INTERMEDIATE_SHA256,
                "scope": "fixed_reviewed_2014_2023_release"}
        await writer.write(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode(),
                           source_url=CATALOGUE_URL, kind="archive_json",
                           metadata={"endpoint": "ar_hiv_collection_plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    endpoints = {"ar_hiv_catalogue", "ar_hiv_csv", "ar_hiv_xlsx", "ar_hiv_tls_intermediate", "ar_hiv_collection_plan"}
    selected = {}
    for artifact in artifacts:
        endpoint = artifact.metadata_.get("endpoint")
        if endpoint in endpoints:
            if endpoint in selected:
                raise ArgentinaHIVContractError(f"Duplicate captured endpoint: {endpoint}")
            selected[endpoint] = artifact
    if set(selected) != endpoints:
        raise ArgentinaHIVContractError("Complete catalogue, CSV, XLSX, transport certificate and plan required")
    payloads = {key: read_artifact(item) for key, item in selected.items()}
    package, resources = discover_resources(payloads["ar_hiv_catalogue"])
    plan = json.loads(payloads["ar_hiv_collection_plan"])
    if (plan.get("package_id") != package["id"] or plan.get("resources") != resources
            or plan.get("catalog_sha256") != selected["ar_hiv_catalogue"].sha256):
        raise ArgentinaHIVContractError("Collection plan no longer matches archived catalogue")
    if plan.get("scope") != "fixed_reviewed_2014_2023_release":
        raise ArgentinaHIVContractError("Reviewed source-release scope changed")
    for kind in ("csv", "xlsx"):
        artifact = selected[f"ar_hiv_{kind}"]
        expected = plan["captures"][kind]
        if artifact.source_url != expected["url"] or artifact.sha256 != expected["sha256"] or artifact.byte_size != expected["byte_size"]:
            raise ArgentinaHIVContractError(f"Archived {kind} does not match collection plan")
        if artifact.source_url != resources[kind]["url"] or artifact.metadata_.get("resource_id") != resources[kind]["id"]:
            raise ArgentinaHIVContractError(f"Archived {kind} resource identity changed")
    if (plan.get("intermediate_sha256") != INTERMEDIATE_SHA256
            or hashlib.sha256(payloads["ar_hiv_tls_intermediate"]).hexdigest() != INTERMEDIATE_SHA256):
        raise ArgentinaHIVContractError("Archived transport certificate changed")
    rows, audit = parse_hiv_payload(payloads["ar_hiv_csv"], payloads["ar_hiv_xlsx"],
                                    retrieved_at=selected["ar_hiv_csv"].metadata_.get("retrieved_at"),
                                    catalog_sha256=selected["ar_hiv_catalogue"].sha256)
    audit.update({"catalogue_url": CATALOGUE_URL, "resources": resources,
                  "archive_scope": plan["scope"], "tls_verification": "verified_existing_roots_and_pinned_intermediate"})
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
