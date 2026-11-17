#!/usr/bin/env python3
"""Durably import verified high-frequency captures and independently check facts.

The plan is a JSON list of {country_code, source, manifest[, start_year]}.
Every replay is checksum verified, uses ordinary staged ingestion and a fenced
foreground task lease, and retains previously stored history.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib import import_module
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select, text
from src.core.database import get_db, dispose_database
from src.core.high_frequency_sources import high_frequency_source, make_updater
from src.domain import Task, Country
from src.domain.task import TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance
from scripts.onboard_remaining_surveillance import (
    bootstrap, fail_foreground_import,
)


def source_tasks_conflict(existing_source, requested_source):
    """Unscoped tasks may overlap; distinct explicit feeds have separate facts."""
    return str(existing_source or "all").lower() in {"all", requested_source}


async def create_source_import_task(code, scope):
    """Reserve this source; ordinary country mutation locks serialize commits.

    Long queued annual downloads do not prevent collecting a distinct weekly
    feed. No task, lease or write lock belonging to another importer is changed.
    """
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(
            Country.code == code).with_for_update(key_share=True))).scalar_one()
        tasks = (await db.execute(select(Task).where(
            Task.country_id == country_id, Task.task_type == TaskType.CRAWL_DATA,
            Task.status.in_([TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RETRYING]),
        ))).scalars().all()
        conflicts = [t.task_uuid for t in tasks if source_tasks_conflict(
            (t.input_data or {}).get("source"), scope)]
        if conflicts:
            raise ValueError(f"{code}/{scope} already has overlapping tasks: {conflicts}")
        now = datetime.now(timezone.utc)
        task = Task(task_type=TaskType.CRAWL_DATA, country_id=country_id,
                    task_name=f"{code} {scope} verified high-frequency import",
                    status=TaskStatus.RUNNING, started_at=now,
                    input_data={"country_code": code, "source": scope, "process": True,
                                "manual_trigger": True, "mapping_notifications_enabled": False},
                    tags=["high-frequency", "foreground", "verified-capture"])
        db.add(task)
        await db.flush()
    return task


class HighFrequencyReplay:
    def __init__(self, connector, manifest):
        self.connector = connector
        document = json.loads(Path(manifest).read_text())
        items = document if isinstance(document, list) else document["artifacts"]
        self.originals = [SimpleNamespace(**item) for item in items]
        if not self.originals:
            raise ValueError("Empty captured-artifact inventory")

    async def collect(self, writer, **options):
        for artifact in self.originals:
            await writer.write(read_artifact(artifact), source_url=artifact.source_url,
                               kind=artifact.kind, metadata=artifact.metadata_)
        return writer.artifacts

    def organize(self, artifacts, **options):
        return self.connector.organize(artifacts, **options)


def row_identity(row):
    dimensions = row.get("Dimensions") or {}
    if isinstance(dimensions, str):
        dimensions = json.loads(dimensions)
    return tuple(str(row.get(key) or "") for key in (
        "Date", "SourceDiseaseCode", "DefinitionVersion", "GeographyKey",
        "Measure", "Frequency", "ReportingBasis",
    )) + (json.dumps(dimensions, sort_keys=True, ensure_ascii=False),)


def numeric_value(row):
    value = str(row.get("Cases", "")).strip()
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


async def verify_capture(updater, rows):
    expected = {row_identity(row): row for row in rows}
    if len(expected) != len(rows):
        raise ValueError("Capture contains duplicate source-native observation identities")
    async with get_db() as db:
        stored = (await db.execute(text("""
            SELECT o.raw_data, o.value, o.time, o.geography_key, o.suppressed,
                   s.metric_type, s.unit AS series_unit, o.unit
            FROM disease_series_observations o
            JOIN disease_surveillance_series s USING(series_code)
            WHERE s.country_code=:country AND s.source_system=:source
        """), {"country": updater.country_code, "source": updater.ontology_source_id})).mappings().all()
    actual, duplicates, invalid = {}, [], []
    for item in stored:
        raw = item["raw_data"] or {}
        key = row_identity(raw)
        if key in actual:
            duplicates.append(key)
        actual[key] = raw
        number = numeric_value(raw)
        stored_value = Decimal(str(item["value"])) if item["value"] is not None else None
        if (number != stored_value
            or str(raw.get("Date")) != item["time"].date().isoformat()
            or str(raw.get("GeographyKey")) != item["geography_key"]
            or raw.get("Measure") != item["metric_type"]
            or raw.get("Unit") != item["unit"]
            or item["unit"] != item["series_unit"]):
            invalid.append(key)
    missing = set(expected) - set(actual)
    mismatch = [key for key in set(expected) & set(actual)
                if any(actual[key].get(field) != value for field, value in expected[key].items())]
    result = {"country_code": updater.country_code, "source_id": updater.ontology_source_id,
              "checked_at": datetime.now(timezone.utc).isoformat(),
              "captured_observations": len(rows), "database_observations": len(stored),
              "numeric_observations": sum(numeric_value(row) is not None for row in rows),
              "retained_history_observations": len(set(actual) - set(expected)),
              "missing_count": len(missing), "mismatch_count": len(mismatch),
              "duplicate_count": len(duplicates), "invalid_count": len(invalid),
              "missing_sample": sorted(missing)[:3], "mismatch_sample": mismatch[:3],
              "invalid_sample": invalid[:3], "duplicate_sample": duplicates[:3],
              "pass": not (missing or mismatch or duplicates or invalid)}
    if not result["pass"]:
        raise ValueError(f"Database acceptance failed: {json.dumps(result, ensure_ascii=False)}")
    return result


async def import_one(item):
    code, scope = item["country_code"].upper(), item["source"]
    spec = high_frequency_source(code, scope)
    if spec is None:
        raise ValueError(f"Unknown high-frequency contract: {code}/{scope}")
    updater = make_updater(code, scope)
    connector = import_module(f"src.services.ingestion.countries.{spec.module}")
    if item.get("manifest"):
        connector = HighFrequencyReplay(connector, item["manifest"])
    options = {key: item[key] for key in ("start_year", "end_year") if key in item}
    options["country_code"] = code
    task = await create_source_import_task(code, scope)
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=connector, source=scope, force=False, process=True,
                connector_options=options)
            from src.domain.collection import SourceArtifact
            async with get_db() as db:
                artifacts = (await db.execute(select(SourceArtifact).where(
                    SourceArtifact.task_uuid == task.task_uuid).order_by(SourceArtifact.id))).scalars().all()
            organized = await asyncio.to_thread(connector.organize, artifacts,
                updater=updater, source=scope, **options)
            acceptance = await verify_capture(updater, organized.rows)
            receipt = {"country_code": code, "source": scope, "task_uuid": task.task_uuid,
                       "status": "completed", "ingestion": asdict(result),
                       "source_latest_date": str(organized.source_latest_date),
                       "source_audit": organized.source_audit, "acceptance": acceptance}
            async with get_db() as db:
                owned = await lease.owned_task(db)
                owned.status = TaskStatus.COMPLETED
                owned.progress = 100
                owned.completed_at = datetime.now(timezone.utc)
                owned.output_data = receipt
        return receipt
    except BaseException as error:
        await fail_foreground_import(task.task_uuid, lease, error)
        raise


async def main(args):
    plan = json.loads(args.plan.read_text())
    if args.countries:
        plan = [item for item in plan if item["country_code"] in args.countries]
    codes = sorted({item["country_code"] for item in plan})
    await bootstrap(codes)
    receipts = []
    try:
        # Sources within a country serialize through the same task/mutation
        # locks; country groups may run concurrently with bounded memory.
        semaphore = asyncio.Semaphore(args.concurrency)
        async def country(code):
            async with semaphore:
                for item in plan:
                    if item["country_code"] != code:
                        continue
                    try:
                        receipt = await import_one(item)
                    except Exception as error:
                        receipt = {"country_code": code, "source": item["source"],
                                   "status": "failed", "error": str(error)}
                    receipts.append(receipt)
                    args.receipt.parent.mkdir(parents=True, exist_ok=True)
                    args.receipt.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, default=str) + "\n")
                    print(json.dumps({key: receipt.get(key) for key in (
                        "country_code", "source", "task_uuid", "status", "source_latest_date", "error")}, ensure_ascii=False), flush=True)
        await asyncio.gather(*(country(code) for code in codes))
    finally:
        await dispose_database()
    return int(any(receipt["status"] != "completed" for receipt in receipts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--countries", nargs="+")
    parser.add_argument("--concurrency", type=int, default=2, choices=range(1, 5))
    sys.exit(asyncio.run(main(parser.parse_args())))
