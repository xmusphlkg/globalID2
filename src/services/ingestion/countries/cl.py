"""Durable capture and offline organization of Chile's official ENO CSV."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.cl import (CATALOG_URL, DEFAULT_SCOPE, ChileENOContractError,
                                  ChileENOCrawler, ChileENOTransportCache, discover_resources, parse_eno_csv)
from src.data.processors.cl import ChileENOHistoricalUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=2007, end_year=None,
                  crawler_type=ChileENOCrawler, cache_ttl_hours=24, force_refresh=False, **options):
    ChileENOHistoricalUpdater.normalize_source(source)
    crawler = crawler_type()
    try:
        catalog = await asyncio.to_thread(crawler.get, CATALOG_URL)
        package, resources = discover_resources(catalog.content)
        await writer.write(catalog.content, source_url=CATALOG_URL, kind="archive_json",
                           metadata={"endpoint": "catalog", "retrieved_at": datetime.now(timezone.utc).isoformat()})
        captures = {}
        for kind in ("xlsx", "pdf", "csv"):
            resource = resources[kind]
            receipt = None
            if kind == "csv":
                payload, receipt = await asyncio.to_thread(
                    ChileENOTransportCache().get, crawler, resource["url"],
                    ttl_hours=cache_ttl_hours, force_refresh=force_refresh or bool(options.get("force")),
                    revision_marker=f"{package.get('metadata_modified')}:{resource['last_modified']}")
            else:
                response = await asyncio.to_thread(crawler.get, resource["url"])
                payload = response.content
            if kind == "csv" and (len(payload) < 100 or b";" not in payload[:4096]):
                raise ChileENOContractError("Official CSV download is not an ENO table")
            if kind == "xlsx" and not payload.startswith(b"PK"):
                raise ChileENOContractError("Official variable dictionary is not XLSX")
            if kind == "pdf" and not payload.startswith(b"%PDF"):
                raise ChileENOContractError("Official methodology is not PDF")
            await writer.write(payload, source_url=resource["url"],
                               kind={"xlsx": "workbook_xlsx", "pdf": "cda_pdf", "csv": "historical_csv"}[kind],
                               metadata={"endpoint": kind, "resource_id": resource["id"],
                                         "catalog_last_modified": package.get("metadata_modified"),
                                         "resource_last_modified": resource["last_modified"],
                                         "retrieved_at": (receipt or {}).get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
                                         "revalidated_at": (receipt or {}).get("revalidated_at"),
                                         "cache_status": (receipt or {}).get("cache_status", "live")})
            captures[kind] = {"url": resource["url"], "sha256": hashlib.sha256(payload).hexdigest(),
                              "byte_size": len(payload)}
        plan = {"package_id": package["id"], "catalog_sha256": hashlib.sha256(catalog.content).hexdigest(),
                "resources": resources, "captures": captures, "start_year": int(start_year),
                "end_year": None if end_year is None else int(end_year)}
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=CATALOG_URL,
                           kind="archive_json", metadata={"endpoint": "collection_plan"})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=2007, end_year=None, **options):
    updater.normalize_source(source)
    selected = {}
    for item in artifacts:
        endpoint = item.metadata_.get("endpoint")
        if endpoint in {"catalog", "collection_plan", "csv", "xlsx", "pdf"}:
            if endpoint in selected:
                raise ChileENOContractError(f"Duplicate captured {endpoint} artifact")
            selected[endpoint] = item
    if set(selected) != {"catalog", "collection_plan", "csv", "xlsx", "pdf"}:
        raise ChileENOContractError("Complete official catalog, three resources and plan required")
    package, resources = discover_resources(read_artifact(selected["catalog"]))
    plan = json.loads(read_artifact(selected["collection_plan"]))
    if plan["package_id"] != package["id"] or plan["catalog_sha256"] != selected["catalog"].sha256:
        raise ChileENOContractError("Captured CKAN catalog does not match collection plan")
    if plan["resources"] != resources or plan["start_year"] != int(start_year) or plan["end_year"] != (None if end_year is None else int(end_year)):
        raise ChileENOContractError("Captured resource or year plan changed")
    for kind in ("csv", "xlsx", "pdf"):
        artifact = selected[kind]
        expected = plan["captures"][kind]
        if artifact.source_url != expected["url"] or artifact.sha256 != expected["sha256"] or artifact.byte_size != expected["byte_size"]:
            raise ChileENOContractError(f"Captured {kind} bytes differ from collection plan")
        read_artifact(artifact)
    parser_digest = hashlib.sha256(Path(__file__).read_bytes() + Path(parse_eno_csv.__code__.co_filename).read_bytes()).hexdigest()
    cache_key = hashlib.sha256(json.dumps([selected["csv"].sha256, parser_digest, int(start_year), end_year], sort_keys=True).encode()).hexdigest()
    cache_dir = Path("data/cache/cl/parsed")
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("raw_sha256") != selected["csv"].sha256 or cached.get("parser_sha256") != parser_digest:
            raise ChileENOContractError("Parsed ENO cache provenance mismatch")
        rows, audit = cached["rows"], cached["audit"]
        audit = audit | {"parse_cache_status": "hit_verified"}
    else:
        rows, audit = parse_eno_csv(read_artifact(selected["csv"]),
                                    source_url=resources["csv"]["url"],
                                    catalog_modified=package.get("metadata_modified"),
                                    retrieved_at=selected["csv"].metadata_.get("retrieved_at"),
                                    start_year=int(start_year),
                                    end_year=None if end_year is None else int(end_year))
        cache_dir.mkdir(parents=True, exist_ok=True)
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(dir=cache_dir, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"raw_sha256": selected["csv"].sha256, "parser_sha256": parser_digest,
                       "rows": rows, "audit": audit}, stream, ensure_ascii=False)
        temporary.replace(cache_path)
        audit["parse_cache_status"] = "parsed_fresh"
    audit.update(official_resource_count=3, catalog_url=CATALOG_URL,
                 catalog_sha256=selected["catalog"].sha256,
                 dictionary_sha256=selected["xlsx"].sha256,
                 methodology_sha256=selected["pdf"].sha256,
                 catalog_last_modified=package.get("metadata_modified"),
                 requested_start_year=int(start_year), requested_end_year=end_year,
                 transport_cache_status=selected["csv"].metadata_.get("cache_status"),
                 archival_completeness="all three currently linked official resources captured")
    return updater.prepare_rows(rows, source=source, source_audit=audit)
