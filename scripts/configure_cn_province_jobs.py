#!/usr/bin/env python3
"""Preview or configure 31 independent weekly Chinese province monthly jobs.

New jobs are disabled until --enable supplies explicitly verified provinces (or
a checked rows package). Existing operator pauses survive regular maintenance.
Data Center releases use one all-province CLI import, never 31 scheduled copies.
This script does not run collections, import facts, or enable the global scheduler.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.data.crawlers.cn_province_adapters import province_adapter_registry  # noqa: E402
from src.domain import AutomationJob  # noqa: E402
from src.services.automation_service import automation_service  # noqa: E402


def desired_jobs(verified_provinces=()) -> dict[str, dict[str, object]]:
    adapters = province_adapter_registry()
    verified = set(verified_provinces)
    unknown = verified - adapters.keys()
    if unknown:
        raise ValueError(f"Unknown verified provinces: {sorted(unknown)}")
    return {
        f"{code.lower()}-monthly-weekly": {
            "name": f"{adapter.name_en} Monthly Report Weekly Check",
            "country_code": code, "source": "cn_province_monthly_report",
            "enabled": code in verified and bool(adapter.index_url)
                       and adapter.parser != "discovery_pending",
            "priority": "normal", "process": True, "save_raw": True,
            "fill_missing": True, "force": False, "include_current_month": False,
            "revision_window_months": 3, "retry_threshold": 3,
            "interval_minutes": 7 * 24 * 60, "daily_time": None,
            "timezone": "Asia/Shanghai",
            "notes": (
                "Check the three most recent distinct published complete months. "
                "Collection errors and sparse narrative evidence remain explicit in source_audit; "
                "At most three discovered case-empty historical months are retried per run, "
                "with a 180-day attempted-month cooldown. Unpublished or summary-only "
                "reports remain unknown and need a separately reviewed backfill. "
                + ("No official monthly entry is configured; keep disabled."
                   if not adapter.index_url or adapter.parser == "discovery_pending" else
                   "Enable only after the current entry and parsed disease/month/metric scope have been verified.")
            ),
        }
        for code, adapter in sorted(adapters.items())
    }


def verified_from_package(path: Path) -> set[str]:
    from scripts.update_cn_provinces import load_rows_package, preflight_rows

    package = load_rows_package(path)
    preflight_rows(package["rows"])
    usable = {item["province"] for item in package.get("collection_audit", {}).get("monthly_jurisdictions", [])
              if item.get("status") in {"parsed", "narrative_sparse"} and not item.get("errors")}
    return {str(row["JurisdictionCode"]) for row in package["rows"]
            if row["SourceID"] == "SRC_CN_PROV_MONTHLY_REPORT" and row["JurisdictionCode"] in usable}


@dataclass(frozen=True)
class JobChange:
    job_id: str
    action: str
    changed_fields: tuple[str, ...]
    enabled: bool


async def configure(*, apply=False, enable=False, verified_provinces=()):
    verified = set(verified_provinces)
    if enable and not verified:
        raise ValueError("--enable requires verified provinces or a validated monthly rows package")
    desired = desired_jobs(verified if enable else ())
    adapters = province_adapter_registry()
    async with get_database() as db:
        existing = {row.job_id: row for row in (
            await db.execute(select(AutomationJob).where(AutomationJob.job_id.in_(tuple(desired))))
        ).scalars().all()}
        changes = []
        for job_id, defaults in desired.items():
            job = existing.get(job_id)
            values = dict(defaults)
            adapter = adapters[str(values["country_code"])]
            unavailable = not adapter.index_url or adapter.parser == "discovery_pending"
            if job is not None and not unavailable:
                # A scoped enable only changes the explicitly reviewed provinces;
                # pauses and other existing settings are not interpreted as consent.
                if not enable or values["country_code"] not in verified:
                    values["enabled"] = job.enabled
            changed = tuple(field for field, value in values.items()
                            if job is None or getattr(job, field) != value)
            changes.append(JobChange(job_id, "create" if job is None else "update" if changed else "unchanged",
                                     changed, bool(values["enabled"])))
            if not apply or not changed:
                continue
            if job is None:
                db.add(AutomationJob(job_id=job_id, **values))
            else:
                for field in changed:
                    setattr(job, field, values[field])
        if apply and any(change.changed_fields for change in changes):
            await db.commit()
    if apply:
        for change in changes:
            if change.changed_fields:
                await automation_service.reschedule_job(change.job_id)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the previewed job changes")
    parser.add_argument("--enable", action="store_true", help="Enable only the explicitly verified monthly sources")
    parser.add_argument("--verified-provinces", default="", help="Comma-separated province codes reviewed against current official reports")
    parser.add_argument("--verified-rows-file", type=Path, help="Checksummed monthly candidate from update_cn_provinces.py")
    args = parser.parse_args()
    verified = {code.strip().upper() for code in args.verified_provinces.split(",") if code.strip()}
    if args.verified_rows_file:
        verified.update(verified_from_package(args.verified_rows_file))
    changes = asyncio.run(configure(apply=args.apply, enable=args.enable, verified_provinces=verified))
    print(json.dumps({"mode": "apply" if args.apply else "preview",
        "jobs": [asdict(change) for change in changes],
        "datacenter": {
            "scheduled_copies": 0,
            "central_refresh": "venv/bin/python scripts/update_cn_provinces.py datacenter --output-rows PATH",
            "scope": "One download per disease/month for all 31 provinces; complete closed years and closed current-year months",
            "cadence_recommendation": "quarterly availability review; scheduling this separate shared import is explicit",
        }}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
