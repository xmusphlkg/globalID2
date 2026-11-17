#!/usr/bin/env python3
"""Replay saved scheduler artifacts and compare all 21 refreshed sources to DB.

No collection or database writes are performed. The existing onboarding row
verifier is reused, with its per-country output redirected into a temporary
directory; only --receipt is persisted. Run after both runtime reports exist.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text
from scripts import onboard_remaining_surveillance as onboarding
from src.core.database import dispose_database, get_db
from src.core.remaining_country_sources import EXTRA_SOURCE_CONTRACTS, REMAINING_COUNTRIES, source_contract
from src.domain import Country, Task
from src.domain.collection import RunStage, SourceArtifact
from src.domain.task import TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact


@asynccontextmanager
async def read_only_db():
    async with get_db() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        yield db


def enum_value(value):
    return getattr(value, "value", value)


def expected_scopes():
    return {(code, source_contract(code)["scope"]) for code in REMAINING_COUNTRIES} | set(EXTRA_SOURCE_CONTRACTS)


def load_jobs(paths):
    jobs, inputs, errors = {}, [], []
    for path in paths:
        try:
            payload = path.read_bytes()
            document = json.loads(payload)
            rows = document["jobs"]
            inputs.append({"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                           "runtime_checked_at": document.get("checked_at"), "jobs": len(rows)})
            for row in rows:
                key = (row["country_code"].upper(), row["scope"])
                if key in jobs:
                    raise ValueError(f"Duplicate runtime source: {key}")
                jobs[key] = row
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{path}: {exc}")
    return jobs, inputs, errors


async def verify_job(code, scope, runtime, scratch):
    evidence = {"country_code": code, "scope": scope, "task_uuid": runtime.get("task_uuid"),
                "job_id": runtime.get("job_id"), "pass": False}
    try:
        task_uuid = runtime["task_uuid"]
        async with read_only_db() as db:
            saved = (await db.execute(select(Task, Country.code).join(
                Country, Country.id == Task.country_id).where(Task.task_uuid == task_uuid))).one()
            task, actual_country = saved
            stages = (await db.execute(select(RunStage).where(
                RunStage.task_uuid == task_uuid).order_by(RunStage.attempt, RunStage.id))).scalars().all()
            if not stages:
                raise ValueError("Task has no ingestion stages")
            attempt = max(stage.attempt for stage in stages)
            current = {stage.stage: stage for stage in stages if stage.attempt == attempt}
            artifacts = (await db.execute(select(SourceArtifact).where(
                SourceArtifact.task_uuid == task_uuid, SourceArtifact.attempt == attempt,
                SourceArtifact.stage == "collect").order_by(SourceArtifact.id))).scalars().all()
        task_input = task.input_data or {}
        evidence.update(task_status=enum_value(task.status), attempt=attempt,
                        task_completed_at=task.completed_at.isoformat() if task.completed_at else None,
                        scheduled_trigger=task_input.get("scheduled_trigger"),
                        stages={name: {"status": stage.status, "metrics": stage.metrics}
                                for name, stage in current.items()})
        if (actual_country != code or enum_value(task.task_type) != TaskType.CRAWL_DATA.value
                or task_input.get("country_code") != code or task_input.get("source") != scope):
            raise ValueError("Runtime source does not match the saved task country/type/source")
        if task.status != TaskStatus.COMPLETED or not task.completed_at:
            raise ValueError("Saved task has not completed")
        if not task_input.get("scheduled_trigger") or not task_input.get("process"):
            raise ValueError("Task was not a scheduled refresh with database processing enabled")
        if task_input.get("automation_job_id") != runtime.get("job_id"):
            raise ValueError("Task automation job differs from runtime report")
        if set(current) != {"collect", "organize", "store"} or any(
                stage.status != "completed" for stage in current.values()):
            raise ValueError("Latest task attempt does not have three completed ingestion stages")
        if not artifacts or current["collect"].metrics.get("artifact_count") != len(artifacts):
            raise ValueError("Saved artifact count differs from completed collection metrics")
        if current["store"].metrics.get("process") is not True:
            raise ValueError("Completed store stage did not process data")
        # Check every registered original, including metadata not yielding rows.
        for artifact in artifacts:
            read_artifact(artifact)
        evidence["artifacts"] = [{"id": a.id, "sha256": a.sha256, "byte_size": a.byte_size,
                                  "relative_path": a.relative_path, "kind": a.kind,
                                  "source_url": a.source_url} for a in artifacts]
        contract = source_contract(code, scope)
        module = contract.get("module", "who_annual" if scope == "who_gho_annual" else "spf_overseas")
        updater_name = contract.get("updater", "WHOAnnualUpdater" if module == "who_annual" else "SPFOverseasUpdater")
        updater = CrawlService._make_updater(updater_name, code)
        connector = import_module(f"src.services.ingestion.countries.{module}")
        options = {"country_code": code}
        if task_input.get("start_year") is not None:
            options["start_year"] = int(task_input["start_year"])
        if (code, scope) in EXTRA_SOURCE_CONTRACTS:
            options["force"] = bool(task_input.get("force"))
        # Replaying captured artifacts must not accidentally fetch current data.
        with patch("requests.sessions.Session.request", side_effect=RuntimeError("Offline verification forbids HTTP requests")):
            fetched = await asyncio.to_thread(connector.organize, artifacts, updater=updater,
                                              source=scope, **options)
        if current["organize"].metrics.get("rows") != len(fetched.rows):
            raise ValueError("Replay row count differs from completed organize metrics")
        evidence.update(source_id=updater.ontology_source_id, source_observations=len(fetched.rows),
                        captured_artifacts=len(artifacts), source_audit=fetched.source_audit,
                        first_observation=min(row["Date"] for row in fetched.rows),
                        last_observation=max(row["Date"] for row in fetched.rows),
                        compared_fields=sorted({field for row in fetched.rows for field in row}),
                        replay_rows_sha256=hashlib.sha256(json.dumps(
                            sorted(fetched.rows, key=onboarding.row_key), sort_keys=True,
                            ensure_ascii=False).encode()).hexdigest())
        acceptance_path = scratch / "data/current" / code.lower() / f"{updater.source_scope}_database_acceptance.json"
        acceptance_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the established verifier's all-field comparisons and integrity
        # checks, without overwriting production data/current receipts.
        try:
            with patch.object(onboarding, "ROOT", scratch), patch.object(onboarding, "get_db", read_only_db):
                acceptance = await onboarding.verify_rows(
                    code, updater, fetched.rows, allow_retained_history=True)
        except Exception:
            if acceptance_path.exists():
                evidence["database_acceptance"] = json.loads(acceptance_path.read_text())
            raise
        evidence.update(database_acceptance=acceptance, **{"pass": acceptance["pass"]})
    except Exception as exc:
        evidence["error"] = str(exc)
    return evidence


async def execute(args):
    jobs, inputs, errors = load_jobs(args.runtime)
    required = expected_scopes()
    missing = sorted(required - set(jobs))
    unexpected = sorted(set(jobs) - required)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "verification": "offline task-artifact replay and all-field database comparison",
              "retained_history_policy": "Older stored observations absent from this capture may remain under upsert semantics; they are counted separately and are not claimed as reverified source facts in this batch.",
              "database_access": "READ ONLY transactions", "runtime_inputs": inputs,
              "required_sources": len(required), "missing_sources": missing,
              "unexpected_sources": unexpected, "input_errors": errors, "sources": []}
    try:
        with TemporaryDirectory(prefix="remaining-refresh-verification-") as directory:
            scratch = Path(directory)
            for code, scope in sorted(required & set(jobs)):
                result = await verify_job(code, scope, jobs[(code, scope)], scratch)
                report["sources"].append(result)
                print(json.dumps({key: result.get(key) for key in (
                    "country_code", "scope", "task_uuid", "source_observations", "pass", "error")},
                    ensure_ascii=False), flush=True)
    finally:
        await dispose_database()
    report["pass"] = not (errors or missing or unexpected) and len(report["sources"]) == len(required) and all(
        source["pass"] for source in report["sources"])
    report["passed_sources"] = sum(source["pass"] for source in report["sources"])
    report["verified_observations"] = sum(source.get("source_observations", 0) for source in report["sources"] if source["pass"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("pass", "passed_sources", "required_sources", "verified_observations")}), flush=True)
    return report["pass"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", nargs="+", type=Path, default=[
        ROOT / "reports/onboarding/remaining_surveillance_runtime.json",
        ROOT / "reports/onboarding/remaining_extra_surveillance_runtime.json"])
    parser.add_argument("--receipt", type=Path,
                        default=ROOT / "reports/onboarding/remaining_refresh_data_acceptance.json")
    args = parser.parse_args()
    if not asyncio.run(execute(args)):
        raise SystemExit("Refresh data verification failed; inspect the receipt")


if __name__ == "__main__":
    main()
