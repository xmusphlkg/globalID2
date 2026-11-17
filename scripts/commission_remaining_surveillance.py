#!/usr/bin/env python3
"""Run and inspect real scheduler-created refreshes for the remaining countries.

--arm advances only enabled selected jobs. Default is read-only verification;
enqueue success alone never counts as a successful refresh.
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
from src.control_plane.schedule_state import schedule_state_repository
from src.core.database import get_db, dispose_database
from src.core.remaining_country_sources import REMAINING_COUNTRIES, EXTRA_SOURCE_CONTRACTS, source_contract
from src.domain import AutomationJob, ScheduledJobState, Task
from src.domain.collection import RunStage


async def execute(args):
    try:
        targets = [(code, source_contract(code)["scope"]) for code in args.countries]
        if args.extras:
            targets = [(code, scope) for code, scope in EXTRA_SOURCE_CONTRACTS if code in args.countries]
        keys = {f"{code.lower()}-{scope.replace('_', '-')}-daily": (code, scope) for code, scope in targets}
        if not keys:
            raise ValueError("No source jobs selected")
        if args.arm:
            async with get_db() as db:
                jobs = (await db.execute(select(AutomationJob).where(AutomationJob.job_id.in_(keys)))).scalars().all()
                if {job.job_id for job in jobs} != set(keys) or any(not job.enabled for job in jobs):
                    raise ValueError("Commissioning requires all selected jobs to exist and be enabled")
            for job_id in keys:
                advanced = await schedule_state_repository.schedule_earlier(
                    "ingestion", job_id, datetime.now(timezone.utc) - timedelta(seconds=5))
                if not advanced:
                    raise ValueError(f"Could not advance schedule for {job_id}; inspect persisted state")
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps({"mode": "armed", "jobs": list(keys),
                "armed_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
            print(json.dumps({"mode": "armed", "jobs": len(keys)}))
            return
        output = []
        async with get_db() as db:
            for job_id, (code, scope) in keys.items():
                job = (await db.execute(select(AutomationJob).where(AutomationJob.job_id == job_id))).scalar_one()
                state = (await db.execute(select(ScheduledJobState).where(
                    ScheduledJobState.job_kind == "ingestion", ScheduledJobState.job_id == job_id))).scalar_one_or_none()
                task = None if state is None or not state.last_task_uuid else (await db.execute(
                    select(Task).where(Task.task_uuid == state.last_task_uuid))).scalar_one_or_none()
                stages = [] if task is None else (await db.execute(select(RunStage).where(
                    RunStage.task_uuid == task.task_uuid).order_by(RunStage.attempt, RunStage.id))).scalars().all()
                latest_attempt = max((stage.attempt for stage in stages), default=0)
                stages = [stage for stage in stages if stage.attempt == latest_attempt]
                inputs = {} if task is None else task.input_data or {}
                # The scheduler passes its trigger evidence through task metadata.
                metadata = {} if task is None else task.metadata_ or {}
                scheduled = bool(inputs.get("scheduled_trigger") or metadata.get("scheduled_trigger"))
                matches_job = (inputs.get("country_code") == code and inputs.get("source") == scope
                               and (inputs.get("automation_job_id") or metadata.get("automation_job_id")) == job_id)
                source_id = source_contract(code, scope)["source_id"]
                count = (await db.execute(text("""SELECT count(*) FROM disease_series_observations obs
                    JOIN disease_surveillance_series series USING(series_code)
                    WHERE series.country_code=:code AND series.source_system=:source_id"""),
                    {"code": code, "source_id": source_id})).scalar_one()
                stage_rows = [{"stage": stage.stage, "status": stage.status,
                               "metrics": stage.metrics, "error": stage.error} for stage in stages]
                complete = (task is not None and str(task.status.value) == "completed" and scheduled and matches_job
                            and {stage.stage for stage in stages if stage.status == "completed"} >= {"collect", "organize", "store"})
                output.append({"country_code": code, "scope": scope, "job_id": job_id,
                    "enabled": job.enabled, "next_run_at": None if state is None else state.next_run_at,
                    "last_dispatch_status": None if state is None else state.last_status,
                    "task_uuid": None if task is None else task.task_uuid,
                    "task_status": None if task is None else task.status.value,
                    "task_error": None if task is None else task.last_error,
                    "scheduled_trigger": scheduled, "source_observations": count,
                    "task_matches_job": matches_job,
                    "stages": stage_rows, "pass": bool(complete and count and job.enabled and state.next_run_at)})
        receipt = {"checked_at": datetime.now(timezone.utc).isoformat(),
                   "status": "passed" if all(row["pass"] for row in output) else "pending_or_failed",
                   "jobs": output}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, default=str, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": receipt["status"], "jobs": [
            {k: row[k] for k in ("country_code", "scope", "task_status", "scheduled_trigger", "source_observations", "pass", "task_error")}
            for row in output]}, ensure_ascii=False))
        if args.require_complete and receipt["status"] != "passed":
            raise SystemExit("Scheduler commissioning is incomplete")
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+", choices=list(REMAINING_COUNTRIES), default=list(REMAINING_COUNTRIES))
    parser.add_argument("--extras", action="store_true")
    parser.add_argument("--arm", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--receipt", type=Path, default=ROOT / "reports/onboarding/remaining_surveillance_runtime.json")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
