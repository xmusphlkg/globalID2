#!/usr/bin/env python3
"""Read Cloudflare publication metadata without deploying or reading R2 objects.

Output deliberately excludes account/zone IDs, credentials, project environment
variables, deployment metadata and API error messages. Missing permissions are
unknown, never an empty healthy configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
API = "https://api.cloudflare.com/client/v4"


def unknown(reason: str, status: int | None = None) -> dict:
    return {"status": "unknown", "reason": reason, "http_status": status}


def fields(value: dict, names: tuple[str, ...]) -> dict:
    return {name: value[name] for name in names if name in value}


class ReadOnlyCloudflare:
    def __init__(self, token: str, *, session=None):
        self._token = token
        self._session = session or requests.Session()

    def read(self, path: str, params: dict | None = None) -> dict:
        if not self._token:
            return unknown("credentials_unavailable")
        # Paths are built by this module, not accepted as a CLI URL. Refuse
        # redirects so Authorization cannot travel to an unexpected endpoint.
        if not path.startswith(("/zones", "/accounts/")) or ".." in path:
            return unknown("invalid_metadata_path")
        try:
            response = self._session.get(API + path, params=params,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=20, allow_redirects=False)
            if response.status_code in (401, 403):
                return unknown("permission_denied", response.status_code)
            if response.status_code != 200:
                return unknown("not_visible_or_not_configured" if response.status_code == 404 else "api_error", response.status_code)
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("success") is not True or "result" not in payload:
                return unknown("invalid_api_response", response.status_code)
            return {"status": "known", "data": payload["result"], "pagination": payload.get("result_info") or {}}
        except (requests.RequestException, ValueError):
            # Exception strings can contain request URLs, headers, or upstream
            # bodies. Only a controlled reason is retained.
            return unknown("request_failed")

    def listing(self, path: str, params: dict | None = None, *, collection: str | None = None) -> dict:
        query = dict(params or {})
        result: list = []
        seen_cursors = set()
        for _ in range(20):
            response = self.read(path, query)
            if response["status"] != "known":
                return {**response, "data": result}
            data = response["data"]
            page = data.get(collection) if collection and isinstance(data, dict) else data
            if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                return {**unknown("invalid_api_response"), "data": result}
            result.extend(page)
            info = response["pagination"]
            if not isinstance(info, dict):
                return {**unknown("invalid_pagination"), "data": result}
            cursor = info.get("cursor")
            if cursor:
                if not isinstance(cursor, str) or cursor in seen_cursors:
                    return {**unknown("invalid_pagination"), "data": result}
                seen_cursors.add(cursor)
                query["cursor"] = cursor
            elif "total_pages" in info:
                current_page, total_pages = info.get("page", 1), info["total_pages"]
                if type(current_page) is not int or type(total_pages) is not int or current_page < 1 or total_pages < 0:
                    return {**unknown("invalid_pagination"), "data": result}
                if total_pages <= current_page:
                    return {"status": "known", "data": result}
                query["page"] = current_page + 1
            elif info.get("is_truncated") is True:
                return {**unknown("incomplete_pagination"), "data": result}
            else:
                return {"status": "known", "data": result}
        return {**unknown("pagination_limit"), "data": result}


def project_result(result: dict, names: tuple[str, ...], *, many: bool = False) -> dict:
    output = {key: value for key, value in result.items() if key not in ("data", "pagination")}
    data = result.get("data")
    if many:
        output["data"] = [fields(row, names) for row in data or [] if isinstance(row, dict)]
    elif isinstance(data, dict):
        output["data"] = fields(data, names)
    elif result["status"] == "known":
        return unknown("invalid_api_response")
    return output


def audit(api: ReadOnlyCloudflare, *, account_id: str, project_name: str, zone_name: str) -> dict:
    report = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "cloudflare_api_metadata",
        "account_ref": hashlib.sha256(account_id.encode()).hexdigest()[:12] if account_id else None,
        "read_only": True,
        "worker_bypass_verified": False,
        "limitations": ["No request metrics or DNS/cache/CSP behavior are measured.",
            "R2 objects and project secrets are not read or exported.",
            "Routes and custom domains alone do not prove zero Worker invocations."],
    }
    zones = api.listing("/zones", {"name": zone_name, "per_page": 50})
    report["zones"] = project_result(zones, ("name", "status", "type"), many=True)
    report["worker_routes"] = []
    report["cache_rules"] = []
    for zone in zones.get("data", []):
        if not isinstance(zone.get("id"), str) or zone.get("name") != zone_name:
            continue
        routes = api.listing(f"/zones/{quote(zone['id'], safe='')}/workers/routes")
        report["worker_routes"].append({"zone": zone_name,
            **project_result(routes, ("pattern", "script"), many=True)})
        ruleset = api.read(f"/zones/{quote(zone['id'], safe='')}/rulesets/phases/http_request_cache_settings/entrypoint")
        cache = project_result(ruleset, ("name", "kind", "phase", "version"))
        if ruleset["status"] == "known":
            rules = ruleset.get("data", {}).get("rules")
            if isinstance(rules, list) and all(isinstance(rule, dict) for rule in rules):
                cache["rules"] = [fields(rule, ("description", "expression", "enabled", "action")) for rule in rules]
            else:
                cache = unknown("invalid_api_response")
        report["cache_rules"].append({"zone": zone_name, **cache})
    if not report["worker_routes"]:
        report["worker_routes"] = [{**unknown("zone_not_visible"), "zone": zone_name}]
        report["cache_rules"] = [{**unknown("zone_not_visible"), "zone": zone_name}]
    if not account_id:
        for section in ("worker_domains", "pages_project", "pages_domains", "r2_buckets"):
            report[section] = unknown("account_unavailable")
        return report
    account = f"/accounts/{quote(account_id, safe='')}"
    report["worker_domains"] = project_result(api.listing(account + "/workers/domains"),
        ("hostname", "service", "environment", "zone_name"), many=True)
    project = account + "/pages/projects/" + quote(project_name, safe="")
    report["pages_project"] = project_result(api.read(project),
        ("name", "subdomain", "domains", "production_branch"))
    report["pages_domains"] = project_result(api.listing(project + "/domains"), ("name", "status"), many=True)
    buckets = api.listing(account + "/r2/buckets", {"per_page": 1000}, collection="buckets")
    report["r2_buckets"] = project_result(buckets, ("name", "location", "storage_class"), many=True)
    for bucket in report["r2_buckets"]["data"]:
        if not isinstance(bucket.get("name"), str):
            continue
        prefix = account + "/r2/buckets/" + quote(bucket["name"], safe="")
        custom = api.read(prefix + "/domains/custom")
        custom_data = custom.get("data")
        if (custom["status"] == "known" and isinstance(custom_data, dict)
                and isinstance(custom_data.get("domains"), list)
                and all(isinstance(domain, dict) for domain in custom_data["domains"])):
            custom["data"] = custom_data["domains"]
            bucket["custom_domains"] = project_result(custom, ("domain", "enabled", "status", "zoneName"), many=True)
        else:
            bucket["custom_domains"] = custom if custom["status"] == "unknown" else unknown("invalid_api_response")
        cors = api.read(prefix + "/cors")
        # CORS is a public policy but project/API envelopes may contain unrelated
        # fields; retain only explicit policy settings, never raw API objects.
        cors_data = cors.get("data")
        if (cors["status"] == "known" and isinstance(cors_data, dict)
                and isinstance(cors_data.get("rules"), list)
                and all(isinstance(rule, dict) and isinstance(rule.get("allowed"), dict) for rule in cors_data["rules"])):
            policies = []
            for rule in cors_data["rules"]:
                policy = fields(rule, ("exposeHeaders", "maxAgeSeconds"))
                if isinstance(rule.get("allowed"), dict):
                    policy["allowed"] = fields(rule["allowed"], ("origins", "methods", "headers"))
                policies.append(policy)
            bucket["cors"] = {"status": "known", "rules": policies}
        else:
            bucket["cors"] = cors if cors["status"] == "unknown" else unknown("invalid_api_response")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zone", default="globalinfectiousdisease.com")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    # Lazy import keeps unit tests independent of application settings and DB.
    from src.services.settings_service import system_settings_service
    config = system_settings_service.cloudflare_runtime()
    token = str(config.get("cloudflare_api_token") or "")
    account = str(config.get("cloudflare_account_id") or "")
    report = audit(ReadOnlyCloudflare(token), account_id=account,
        project_name=str(config.get("default_cloudflare_project_name") or "globalid"), zone_name=args.zone)
    # Final defense against an API accidentally echoing credentials in metadata.
    body = json.dumps(report, ensure_ascii=False, indent=2)
    for secret in (token, account):
        if secret:
            body = body.replace(secret, "[redacted]")
    output = args.output or ROOT / "exports/publication-audits" / (datetime.now(timezone.utc).strftime("cloudflare-%Y%m%dT%H%M%S%fZ.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body + "\n")
    print(json.dumps({"report": str(output), "read_only": True, "worker_bypass_verified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
