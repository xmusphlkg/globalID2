#!/usr/bin/env python3
"""Stagger NL's existing 30-minute jobs and attest BQ's real parent refresh.

Preview by default; --apply changes only persisted next_run_at. --verify
checks a scheduler-created annual task after that rephase and replays its raw
HTML against the stored BQ observations. No publication or manual crawl runs.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text
from src.core.country_library import get_country_bootstrap_config
from src.core.database import get_db, dispose_database
from src.domain import AutomationJob, Country, ScheduledJobState, Task
from src.domain.collection import RunStage, SourceArtifact
from src.domain.task import TaskStatus, TaskType
from src.data.processors.nl_notifiable import NLNotifiableAnnualUpdater
from src.services.ingestion.countries.nl_notifiable import organize

JOBS = (
    ("nl-rivm-notifiable-daily", "rivm_notifiable_annual"),
    ("nl-rivm-virological-daily", "rivm_virological_weekly"),
    ("nl-rivm-respiratory-daily", "rivm_respiratory_weekly"),
    ("nl-ecdc-daily", "ecdc_atlas_annual"),
)
ACTIVE = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)


def phase_plan(anchor, gap_minutes=6):
    if not 3 <= gap_minutes <= 7:
        raise ValueError("Use 3–7 minute gaps within the existing 30-minute cycle")
    return {job: anchor + timedelta(minutes=index * gap_minutes)
            for index, (job, _) in enumerate(JOBS)}


def write_receipt(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2) + "\n")


async def rephase(*, apply=False, gap_minutes=6):
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(
            Country.code == "NL").with_for_update(key_share=True))).scalar_one()
        active = (await db.execute(select(Task.task_uuid).where(
            Task.country_id == country_id, Task.task_type == TaskType.CRAWL_DATA,
            Task.status.in_(ACTIVE)))).scalars().all()
        if active:
            return {"status": "waiting_for_idle", "active_tasks": active}
        jobs = (await db.execute(select(AutomationJob).where(
            AutomationJob.job_id.in_([job for job, _ in JOBS])).with_for_update())).scalars().all()
        if {job.job_id for job in jobs} != {job for job, _ in JOBS}:
            raise ValueError("The four existing NL jobs are required")
        by_id = {job.job_id: job for job in jobs}
        for job_id, scope in JOBS:
            job = by_id[job_id]
            if (job.country_code != "NL" or job.source != scope or not job.enabled
                or job.interval_minutes != 30 or job.daily_time is not None):
                raise ValueError(f"Refusing to alter the operator's job settings: {job_id}")
        states = {state.job_id: state for state in (await db.execute(select(ScheduledJobState).where(
            ScheduledJobState.job_kind == "ingestion",
            ScheduledJobState.job_id.in_(by_id)).with_for_update())).scalars().all()}
        if set(states) != set(by_id):
            raise ValueError("Persisted schedule state is required for all four jobs")
        now = datetime.now(timezone.utc)
        # Give any in-flight scheduler tick time to observe the persisted edit.
        planned = phase_plan(now + timedelta(seconds=45), gap_minutes)
        rows = []
        for job_id, scope in JOBS:
            state = states[job_id]
            rows.append({"job_id": job_id, "country_code": "NL", "source": scope,
                         "enabled": by_id[job_id].enabled, "interval_minutes": 30,
                         "previous_next_run_at": state.next_run_at,
                         "previous_task_uuid": state.last_task_uuid,
                         "next_run_at": planned[job_id]})
            if apply:
                state.next_run_at = planned[job_id]
        return {"status": "armed" if apply else "preview", "armed_at": now.isoformat(),
                "gap_minutes": gap_minutes, "jobs": rows,
                "changed_fields": ["scheduled_job_states.next_run_at"],
                "public_release_enabled": False}


async def verify(armed):
    if get_country_bootstrap_config("BQ").get("public_release_enabled", True) is not False:
        raise ValueError("BQ publication must remain disabled")
    async with get_db() as db:
        state = (await db.execute(select(ScheduledJobState).where(
            ScheduledJobState.job_kind == "ingestion", ScheduledJobState.job_id == JOBS[0][0]))).scalar_one()
        task = None if not state.last_task_uuid else (await db.execute(select(Task).where(
            Task.task_uuid == state.last_task_uuid))).scalar_one_or_none()
        inputs = {} if task is None else task.input_data or {}
        matches = (inputs.get("country_code") == "NL" and inputs.get("source") == JOBS[0][1]
                   and inputs.get("automation_job_id") == JOBS[0][0] and inputs.get("scheduled_trigger")
                   and task.created_at >= datetime.fromisoformat(armed["armed_at"]))
        status = None if task is None else task.status.value
        if not matches or status not in {"completed", "failed", "cancelled"}:
            return {"status": "pending", "task_uuid": state.last_task_uuid,
                    "task_status": status, "task_matches_job": bool(matches)}
        if status != "completed":
            raise ValueError(f"NL annual scheduled refresh {task.task_uuid}: {status}: {task.last_error}")
        stages = (await db.execute(select(RunStage).where(
            RunStage.task_uuid == task.task_uuid).order_by(RunStage.attempt, RunStage.id))).scalars().all()
        attempt = max((stage.attempt for stage in stages), default=0)
        stages = [stage for stage in stages if stage.attempt == attempt]
        if {stage.stage for stage in stages if stage.status == "completed"} != {"collect", "organize", "store"}:
            raise ValueError("Scheduled refresh did not complete all three stages")
        artifacts = (await db.execute(select(SourceArtifact).where(
            SourceArtifact.task_uuid == task.task_uuid, SourceArtifact.attempt == attempt))).scalars().all()
        actual = (await db.execute(text("""SELECT obs.raw_data, obs.value FROM disease_series_observations obs
            JOIN disease_surveillance_series series USING(series_code)
            WHERE series.country_code='NL' AND series.source_system='SRC_NL_RIVM_NOTIFIABLE'
            AND obs.geography_key='country:BQ:national'"""))).all()
        jobs = (await db.execute(select(AutomationJob).where(AutomationJob.job_id.in_(
            [job for job, _ in JOBS])))).scalars().all()
        assert all(job.enabled and job.interval_minutes == 30 for job in jobs)
        schedules = (await db.execute(select(ScheduledJobState).where(
            ScheduledJobState.job_kind == "ingestion", ScheduledJobState.job_id.in_(
                [job for job, _ in JOBS])))).scalars().all()
    fetched = await asyncio.to_thread(organize, artifacts, updater=NLNotifiableAnnualUpdater(), source=JOBS[0][1])
    expected = {(row["Date"], row["SourceDiseaseCode"]): row for row in fetched.rows
                if row["GeographyKey"] == "country:BQ:national"}
    stored = {(raw["Date"], raw["SourceDiseaseCode"]): (raw, value) for raw, value in actual}
    if not expected or set(expected) != set(stored) or len(stored) != len(actual):
        raise ValueError("BQ source and database observation identities differ")
    for key, row in expected.items():
        raw, value = stored[key]
        if any(raw.get(field) != expected_value for field, expected_value in row.items()):
            raise ValueError(f"BQ raw source replay differs at {key}")
        if value != (int(row["Cases"]) if row["Cases"].isdigit() else None):
            raise ValueError(f"BQ stored value differs at {key}")
    return {"status": "passed", "checked_at": datetime.now(timezone.utc),
            "task_uuid": task.task_uuid, "task_matches_job": True, "scheduled_trigger": True,
            "task_status": status, "source": inputs["source"], "job_id": JOBS[0][0],
            "stages": [{"stage": stage.stage, "status": stage.status, "metrics": stage.metrics} for stage in stages],
            "artifacts": [{"sha256": a.sha256, "relative_path": a.relative_path, "source_url": a.source_url} for a in artifacts],
            "bq_observations_verified": len(expected), "source_rows": len(fetched.rows),
            "raw_replay_matches_database": True, "public_release_enabled": False,
            "interval_minutes": 30, "phase": armed,
            "persisted_schedule_after": [{"job_id": state.job_id, "next_run_at": state.next_run_at}
                                         for state in schedules]}


async def execute(args):
    try:
        if args.apply or not args.verify:
            deadline = asyncio.get_running_loop().time() + args.wait_idle_seconds
            while True:
                armed = await rephase(apply=args.apply, gap_minutes=args.gap_minutes)
                if armed["status"] != "waiting_for_idle" or asyncio.get_running_loop().time() >= deadline:
                    break
                print(json.dumps(armed), flush=True)
                await asyncio.sleep(10)
            write_receipt(args.receipt, armed)
            print(json.dumps(armed, default=str), flush=True)
            if armed["status"] == "waiting_for_idle":
                raise SystemExit("NL is still busy; schedule was not changed")
        else:
            armed = json.loads(args.receipt.read_text())
            armed = armed.get("phase", armed)
        if args.verify:
            if armed["status"] != "armed":
                raise ValueError("Verification requires a previously applied rephase receipt")
            deadline = asyncio.get_running_loop().time() + args.wait_complete_seconds
            while True:
                result = await verify(armed)
                print(json.dumps(result, default=str), flush=True)
                if result["status"] == "passed":
                    write_receipt(args.receipt, result)
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise SystemExit("NL scheduled annual refresh is still pending")
                await asyncio.sleep(10)
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--gap-minutes", type=int, default=6)
    parser.add_argument("--wait-idle-seconds", type=int, default=0)
    parser.add_argument("--wait-complete-seconds", type=int, default=0)
    parser.add_argument("--receipt", type=Path, default=ROOT / "reports/onboarding/nl_bq_scheduled_refresh.json")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
