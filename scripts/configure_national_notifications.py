#!/usr/bin/env python3
"""Configure KR/TH/MY/NL national notification publication checks.

Dry-run by default. --apply persists jobs; --enable explicitly resumes existing
paused jobs. Country bootstrap and historical imports are separate operations.
Managed jobs retain source revisions; current TH DDS uses bounded per-category
recovery and the configured revision window rather than full-history downloads.
All managed sources use 30-minute control-plane checks.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.domain import AutomationJob  # noqa: E402
from src.services.automation_service import automation_service  # noqa: E402


def desired_jobs() -> dict[str, dict[str, object]]:
    shared = {
        "enabled": True, "priority": "normal", "process": True,
        "save_raw": True, "fill_missing": False, "force": False,
        "include_current_month": False, "revision_window_months": 3,
        "retry_threshold": 3, "interval_minutes": 30,
    }
    return {
        "kr-kdca-monthly-daily": {
            **shared, "name": "Korea KDCA Monthly 30-Minute Publication Check",
            "country_code": "KR", "source": "kdca_open_api",
            "daily_time": None, "timezone": "Asia/Seoul",
            "notes": "Refresh Korea KDCA national monthly notifiable-disease cases. Preserve domestic/imported subtotals, provisional status and source revisions; API credentials or the verified portal fallback are required.",
        },
        "kr-kdca-region-annual-daily": {
            **shared, "name": "Korea KDCA Regional Annual 30-Minute Publication Check",
            "country_code": "KR", "source": "kdca_region_annual",
            "daily_time": None, "timezone": "Asia/Seoul",
            "notes": "Refresh KDCA Region01 annual city/province totals under separate KR-* geography keys. Do not add regional values to national monthly totals or reinterpret reporting area as residence.",
        },
        "kr-kdca-death-annual-daily": {
            **shared, "name": "Korea KDCA Annual Death 30-Minute Publication Check",
            "country_code": "KR", "source": "kdca_death_annual",
            "daily_time": None, "timezone": "Asia/Seoul",
            "notes": "Refresh the independent KDCA death01 annual series. Keep death observations separate from the monthly case source and preserve provisional source semantics.",
        },
        "my-idengue-daily": {
            **shared, "name": "Malaysia iDengue 30-Minute Publication Check",
            "country_code": "MY", "source": "my_idengue_daily",
            "interval_minutes": 30, "daily_time": None, "timezone": "Asia/Kuala_Lumpur",
            "notes": "Capture the explicit source-dated national and source-area daily dengue counts. Keep ambiguous cumulative counts and cumulative deaths outside the daily case series; retain raw evidence and do not manufacture missing historical days.",
        },
        "my-who-flunet-daily": {
            **shared, "name": "Malaysia WHO FluNet 30-Minute Publication Check",
            "country_code": "MY", "source": "who_flunet_weekly",
            "interval_minutes": 30, "daily_time": None, "timezone": "Asia/Kuala_Lumpur",
            "notes": "Refresh the published Malaysian WHO FluNet weekly laboratory history and revisions, preserving reporting origin, pathogen, subtype and denominator identities. Laboratory detections are not national patient notification counts.",
        },
        "my-who-gho-daily": {
            **shared, "name": "Malaysia WHO GHO 30-Minute Publication Check",
            "country_code": "MY", "source": "who_gho_annual",
            "interval_minutes": 30, "daily_time": None, "timezone": "Asia/Kuala_Lumpur",
            "notes": "Check annual WHO GHO reported-infection indicators for new closed years and revisions. Preserve indicator definitions and source provenance; do not replace reported cases with modelled incidence estimates.",
        },
        "nl-rivm-virological-daily": {
            **shared, "name": "Netherlands RIVM Virological 30-Minute Publication Check",
            "country_code": "NL", "source": "rivm_virological_weekly",
            "interval_minutes": 30, "daily_time": None, "timezone": "Europe/Amsterdam",
            "notes": "Refresh every category in the published rolling virological weekly laboratory return. Preserve reporting-laboratory counts, explicit zeroes and original source weeks; retain older stored history after it leaves the upstream window.",
        },
        "nl-rivm-respiratory-daily": {
            **shared, "name": "Netherlands RIVM Respiratory 30-Minute Publication Check",
            "country_code": "NL", "source": "rivm_respiratory_weekly",
            "interval_minutes": 30, "daily_time": None, "timezone": "Europe/Amsterdam",
            "notes": "Resolve official respiratory CKAN releases and refresh verified weekly source observations. Keep incidence, positivity, laboratory detections, admissions and wastewater metrics separate; retain source calendar evidence and definition changes.",
        },
        "th-dds-tableau-daily": {
            **shared, "name": "Thailand DDS Tableau 30-Minute Publication Check",
            "country_code": "TH", "source": "th_dds_tableau",
            "daily_time": None, "timezone": "Asia/Bangkok",
            "notes": "Refresh the anonymous official DDS Tableau across all published categories with a four-week per-category task budget, actual stored coverage recovery across years, and a rotating three-month revision window; preserve source date/place basis, measures and official calendars. Other DDS report and archive contracts retain separate jobs.",
        },
        "th-dds-weekly-daily": {
            **shared, "name": "Thailand DDS Weekly 30-Minute Publication Check",
            "country_code": "TH", "source": "th_dds_weekly",
            "daily_time": None, "timezone": "Asia/Bangkok",
            "notes": "Refresh public DDS weekly situation tables with a four-week per-category task budget, stored coverage recovery across years, and a rotating three-month revision window; preserve every source disease category, offered interval and official calendar. Report 506 historical revisions use their own later job.",
        },
        "th-doe506-daily": {
            **shared, "name": "Thailand DOE 506 30-Minute Publication Check",
            "country_code": "TH", "source": "th_doe506_monthly",
            "daily_time": None, "timezone": "Asia/Bangkok",
            "notes": "Recheck published closed-year Report 506 history and revisions. This historical source does not replace a current DDS feed; unknown cells remain unknown.",
        },
        "th-id-th-history-daily": {
            **shared, "name": "Thailand ID_TH Archive 30-Minute Revision Check",
            "country_code": "TH", "source": "th_id_th_history",
            "daily_time": None, "timezone": "Asia/Bangkok",
            "notes": "Check the user-curated ID_TH archive latest Git commit/tree and fetch only missing or changed Git blobs. Preserve national source weeks, cases and deaths separately; historical calendars and date basis remain unverified, and source-year storage anchors must not be graphed as weekly dates.",
        },
        "my-moh-std-daily": {
            **shared, "name": "Malaysia MOH STD 30-Minute Publication Check",
            "country_code": "MY", "source": "moh_std_annual",
            "daily_time": None, "timezone": "Asia/Kuala_Lumpur",
            "notes": "Refresh the complete MOH annual STD notification release, retaining all source categories and revised historical counts. This feed covers published STD categories, not every Malaysian notifiable disease.",
        },
        "nl-rivm-notifiable-daily": {
            **shared, "name": "Netherlands RIVM Notifiable Diseases 30-Minute Publication Check",
            "country_code": "NL", "source": "rivm_notifiable_annual",
            "daily_time": None, "timezone": "Europe/Amsterdam",
            "notes": "Refresh the complete RIVM annual notifiable disease table, including closed-year additions and historical revisions. Preserve outbreak counts and Caribbean Netherlands identities separately; ECDC baseline uses its own job.",
        },
    }


@dataclass(frozen=True)
class JobChange:
    job_id: str
    action: str
    changed_fields: tuple[str, ...]
    enabled: bool


async def configure(*, apply: bool = False, enable: bool = False) -> list[JobChange]:
    desired = desired_jobs()
    async with get_database() as db:
        existing = {
            row.job_id: row for row in (
                await db.execute(select(AutomationJob).where(AutomationJob.job_id.in_(tuple(desired))))
            ).scalars().all()
        }
        changes = []
        for job_id, defaults in desired.items():
            job = existing.get(job_id)
            values = dict(defaults)
            if job is not None and not enable:
                # A previous operator pause is intentional; regular seed
                # maintenance must never silently resume that ingestion job.
                values["enabled"] = job.enabled
            changed = tuple(field for field, value in values.items() if job is None or getattr(job, field) != value)
            changes.append(JobChange(job_id, "create" if job is None else "update" if changed else "unchanged", changed, bool(values["enabled"])))
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
        # Match the schedule API: commit configuration and release its session
        # before persisting next_run_at with the scheduling service.
        for change in changes:
            if change.changed_fields:
                await automation_service.reschedule_job(change.job_id)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist job changes; otherwise only read and preview")
    parser.add_argument("--enable", action="store_true", help="Explicitly resume existing paused managed jobs (effective with --apply)")
    args = parser.parse_args()
    for change in asyncio.run(configure(apply=args.apply, enable=args.enable)):
        verb = "Applied" if args.apply else "Plan"
        print(f"{verb}: {change.action} {change.job_id}; enabled={str(change.enabled).lower()}; changed_fields={','.join(change.changed_fields) or 'none'}")


if __name__ == "__main__":
    main()
