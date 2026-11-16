"""Capture RIVM releases and chart semantics, then replay without network I/O."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.data.crawlers.nl_weekly import (
    RESP_API,
    RESP_LANDING,
    RESP_PROFILES,
    RESP_SCOPE,
    VIRO_LANDING,
    VIRO_SCOPE,
    VIRO_URL,
    NetherlandsWeeklyCrawler,
    NLWeeklyContractError,
    parse_respiratory_resource,
    parse_virological_csv,
)
from src.services.ingestion.artifacts import read_artifact


def _resource_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "data.rivm.nl" or not parsed.path.startswith("/opendata/"):
        raise NLWeeklyContractError("Respiratory resource must remain on the official HTTPS open-data host")
    return url


async def collect(writer, *, source=VIRO_SCOPE, **options):
    if source not in {VIRO_SCOPE, RESP_SCOPE}:
        raise ValueError(f"Unsupported RIVM weekly scope: {source}")
    crawler = NetherlandsWeeklyCrawler()
    captured = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "as_of": datetime.now(ZoneInfo("Europe/Amsterdam")).date().isoformat()}
    try:
        landing = await asyncio.to_thread(crawler.get, VIRO_LANDING if source == VIRO_SCOPE else RESP_LANDING)
        await writer.write(landing.content, source_url=landing.url, kind="rivm_weekly_landing", metadata=captured)
        if source == VIRO_SCOPE:
            response = await asyncio.to_thread(crawler.get, VIRO_URL)
            await writer.write(response.content, source_url=response.url, kind="rivm_virological_csv", metadata=captured)
        else:
            response = await asyncio.to_thread(crawler.get, RESP_API)
            await writer.write(response.content, source_url=response.url, kind="rivm_respiratory_metadata", metadata=captured)
            data = response.json()
            if not data.get("success") or data.get("result", {}).get("name") != "res-ari-surveillance":
                raise NLWeeklyContractError("Invalid CKAN respiratory package response")
            resources = data["result"]["resources"]
            if len({r["id"] for r in resources}) != len(resources) or len({r["name"] for r in resources}) != len(resources):
                raise NLWeeklyContractError("Duplicate CKAN resource identity")
            for resource in resources:
                # Original CSVs and source chart definitions are both evidence.
                if resource.get("format", "").upper() not in {"CSV", "JSON"}:
                    continue
                response = await asyncio.to_thread(crawler.get, _resource_url(resource["url"]))
                await writer.write(response.content, source_url=response.url,
                    kind="rivm_respiratory_resource", metadata={**captured,
                        "resource_id": resource["id"], "resource_name": resource["name"],
                        "resource_format": resource["format"].upper()})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=VIRO_SCOPE, **options):
    updater.normalize_source(source)
    if source == VIRO_SCOPE:
        csvs = [a for a in artifacts if a.kind == "rivm_virological_csv"]
        if len(csvs) != 1:
            raise NLWeeklyContractError("Exactly one captured virological CSV required")
        artifact = csvs[0]
        rows, audit = parse_virological_csv(read_artifact(artifact),
            as_of=date.fromisoformat(artifact.metadata_["as_of"]))
    else:
        metadata = [a for a in artifacts if a.kind == "rivm_respiratory_metadata"]
        if len(metadata) != 1:
            raise NLWeeklyContractError("Exactly one captured respiratory package required")
        package = json.loads(read_artifact(metadata[0]))
        if not package.get("success"):
            raise NLWeeklyContractError("Unsuccessful captured CKAN response")
        package = package["result"]
        if package.get("name") != "res-ari-surveillance":
            raise NLWeeklyContractError("Captured CKAN package identity changed")
        manifest = package["resources"]
        if len({r["id"] for r in manifest}) != len(manifest) or len({r["name"] for r in manifest}) != len(manifest):
            raise NLWeeklyContractError("Duplicate captured CKAN manifest resource identity")
        resources = {r["name"]: r for r in package["resources"] if r.get("format", "").upper() in {"CSV", "JSON"}}
        captured = [a for a in artifacts if a.kind == "rivm_respiratory_resource"]
        by_name = {a.metadata_["resource_name"]: a for a in captured}
        if len(captured) != len(by_name) or set(resources) != set(by_name):
            raise NLWeeklyContractError("Captured resources do not match the complete package manifest")
        for name, artifact in by_name.items():
            if artifact.metadata_["resource_id"] != resources[name]["id"] or artifact.source_url != resources[name]["url"]:
                raise NLWeeklyContractError("Captured resource identity/URL differs from manifest")
        csv_names = {name for name, r in resources.items() if r["format"].upper() == "CSV"}
        if not set(RESP_PROFILES).issubset(csv_names):
            raise NLWeeklyContractError("Reviewed respiratory resources disappeared")
        rows, reports = [], []
        for name, profile in RESP_PROFILES.items():
            config_name = profile[-1]
            if config_name not in by_name:
                raise NLWeeklyContractError(f"Missing source chart semantics: {config_name}")
            artifact = by_name[name]
            normalized, report = parse_respiratory_resource(read_artifact(artifact),
                resource=resources[name], config=json.loads(read_artifact(by_name[config_name])),
                source_url=artifact.source_url, metadata_modified=package["metadata_modified"])
            rows.extend(normalized)
            reports.append(report)
        audit = {"package_modified": package["metadata_modified"], "captured_csv_resources": len(csv_names),
            "captured_resources": len(captured), "normalized_resources": len(RESP_PROFILES),
            "excluded_resources": [{"resource": n, "reason": "no verified year/season context or unsupported new resource; raw bytes retained"} for n in sorted(csv_names - set(RESP_PROFILES))],
            "resource_audits": reports, "observations": len(rows), "dates_inferred": False,
            "calendar_status": "source_season_week_retained_boundaries_unverified"}
    audit["internal_only"] = True
    return updater.prepare_rows(rows, source=source, source_audit=audit)
