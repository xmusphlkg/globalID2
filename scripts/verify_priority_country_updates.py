#!/usr/bin/env python3
"""Verify saved channels against the live, authenticated panel API.

Read-only. Credentials come from application configuration and are never
included in the local acceptance receipt.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from src.core.config import get_config
from src.core.source_channels import load_source_channels


async def verify(args):
    channels = load_source_channels()
    if args.countries:
        channels = [row for row in channels if row['country_code'] in args.countries]
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(),
                "mode": "readonly_live_panel_verification", "countries": [],
                "jobs": [], "flows": {}, "errors": []}
    async with httpx.AsyncClient(base_url=args.base_url,
                                headers={"x-dashboard-api-key": get_config().dashboard_api_key},
                                timeout=120) as client:
        async def get(path):
            response = await client.get(path)
            response.raise_for_status()
            return response.json()["data"]

        config = {row["country_code"]: row for row in await get("/sources/config?lang=zh")}
        jobs = {row["job_id"]: row for row in await get("/schedules/ingestion/jobs")}
        for channel in channels:
            country, scope = channel["country_code"], channel["scope"]
            job_id = f"{country.lower()}-{scope.replace('_', '-')}-daily"
            row, job = config.get(country), jobs.get(job_id)
            if row is None or job is None:
                evidence["errors"].append(f"{country}: country or managed job missing")
                continue
            evidence["countries"].append(row)
            evidence["jobs"].append(job)
            option = next((x for x in row["source_options"] if x["value"] == scope), {})
            saved = option.get("source_channel") or {}
            policy = option.get("source_policy") or {}
            if any(saved.get(k) != value for k, value in channel.items()
                   if k != "enabled_by_default"):
                evidence["errors"].append(f"{country}: panel channel differs from saved catalogue")
            if not row["supports_crawl"] or policy.get("revision_window_unit") != "years" or policy.get("public_release_enabled") is not False:
                evidence["errors"].append(f"{country}: invalid collection/revision/public policy")
            should_run = channel.get("enabled_by_default", True)
            if (job["enabled"] != should_run or
                    (should_run and not job["next_run_at"]) or
                    (not should_run and job["next_run_at"]) or
                    job["source"] != scope or
                    job["revision_window_months"] != channel["refresh_years"]):
                evidence["errors"].append(f"{country}: managed daily job is not ready")
            flows = await get(f"/sources/flow?country_code={country}")
            evidence["flows"][country] = flows
            matching = [x for x in flows if x["source_scope"] == scope]
            if not matching or any((x.get("source_channel") or {}).get("official_url") != channel["official_url"] for x in matching):
                evidence["errors"].append(f"{country}: source flow/channel missing")
            if args.require_data and not any(x.get("source_observation_count", 0) > 0 for x in matching):
                evidence["errors"].append(f"{country}: no committed source observations")
    evidence["status"] = "passed" if not evidence["errors"] else "failed"
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": evidence["status"], "countries": len(evidence["countries"]),
                      "enabled_jobs": sum(x["enabled"] for x in evidence["jobs"]),
                      "observations": {code: sum(x.get("source_observation_count", 0) for x in rows)
                                       for code, rows in evidence["flows"].items()},
                      "errors": evidence["errors"]}, ensure_ascii=False))
    return bool(evidence["errors"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--require-data", action="store_true")
    parser.add_argument("--countries", nargs="+", help="Limit verification to selected country codes")
    parser.add_argument("--receipt", type=Path, default=ROOT / "exports/priority-country-updates-20261116/live-source-config.json")
    raise SystemExit(asyncio.run(verify(parser.parse_args())))
