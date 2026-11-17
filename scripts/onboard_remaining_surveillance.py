#!/usr/bin/env python3
"""Bootstrap, import and verify remaining regional sources through staged ingestion."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import import_module
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text
from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.core.database import dispose_database, get_db
from src.core.db_schema import ensure_country_scope_for_code, ensure_task_type_enum_schema
from src.core.remaining_country_sources import REMAINING_COUNTRIES, source_contract
from src.domain import Country, CrawlRun, Task
from src.domain.task import TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.foreground import foreground_task_lease
from src.services.ingestion.runner import run_surveillance

CONFIG_FIELDS = ("data_source_url", "data_source_type", "crawler_config", "parser_config",
                 "disease_mapping_rules", "report_config", "notes")


async def bootstrap(codes):
    async with get_db() as db:
        for code in codes:
            country = (await db.execute(select(Country).where(Country.code == code))).scalar_one_or_none()
            config = get_country_bootstrap_config(code)
            if country is None:
                profile = get_country_profile(code)
                country = Country(code=code, name=profile.name, name_en=profile.name_en,
                                  name_local=profile.name_local, language=profile.language,
                                  timezone=profile.timezone)
                for field in CONFIG_FIELDS:
                    if field in config:
                        setattr(country, field, config[field])
                db.add(country)
                await db.flush()
            else:
                # Keep additional sources and operator settings on existing countries.
                crawler = dict(country.crawler_config or {})
                configured = config["crawler_config"]
                crawler["sources"] = list(dict.fromkeys([*crawler.get("sources", []), *configured["sources"]]))
                crawler["source_policies"] = {**crawler.get("source_policies", {}), **configured.get("source_policies", {})}
                country.crawler_config = crawler
            await ensure_country_scope_for_code(db, code)


class CapturedReplay:
    """Copy hash-checked originals into a real task's durable artifact writer."""
    def __init__(self, connector, manifest):
        self.connector = connector
        self.originals = [SimpleNamespace(**row) for row in json.loads(manifest.read_text())]

    async def collect(self, writer, **options):
        for artifact in self.originals:
            await writer.write(read_artifact(artifact), source_url=artifact.source_url,
                               kind=artifact.kind, metadata=artifact.metadata_)
        return writer.artifacts

    def organize(self, artifacts, **options):
        return self.connector.organize(artifacts, **options)


def manifest_for(code, scope=None):
    suffix = {"who_gho_annual": "who_gho_source_audit.json", "spf_odisse_annual": "spf_odisse_source_audit.json", "cr_moh_notifications": "cr_moh_source_audit.json", "who_wpro_dengue": "wpro_dengue_source_audit.json"}[source_contract(code, scope)["scope"]]
    audit_path = ROOT / "data/current" / code.lower() / suffix
    audit = json.loads(audit_path.read_text())
    return ROOT / audit["raw_directory"] / "artifacts_manifest.json"


def row_key(row):
    return tuple(str(row.get(field, "")) for field in (
        "Date", "SourceDiseaseCode", "GeographyKey", "DefinitionVersion",
        "Measure", "ReportingBasis", "Frequency"))


async def verify_rows(code, updater, rows, *, allow_retained_history=False):
    expected = {row_key(row): row for row in rows}
    if len(expected) != len(rows):
        raise ValueError("Source has duplicate observation identities")
    async with get_db() as db:
        actual_rows = (await db.execute(text("""
            SELECT obs.raw_data, obs.value, obs.time, obs.geography_key
            FROM disease_series_observations obs
            JOIN disease_surveillance_series series USING(series_code)
            WHERE series.country_code=:code AND series.source_system=:source_id
        """), {"code": code, "source_id": updater.ontology_source_id})).fetchall()
    actual, duplicate, invalid = {}, [], []
    for raw, value, when, geography in actual_rows:
        key = row_key(raw)
        if key in actual:
            duplicate.append(key)
        actual[key] = raw
        if raw.get("Date") != when.date().isoformat() or raw.get("GeographyKey") != geography:
            invalid.append(key)
        count = str(raw.get("Cases", ""))
        if count.isdigit():
            if value != float(count):
                invalid.append(key)
        elif value is not None:
            invalid.append(key)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatched = [key for key in set(expected) & set(actual)
                  if any(actual[key].get(field) != value for field, value in expected[key].items())]
    receipt = {"country_code": code, "source_id": updater.ontology_source_id,
               "checked_at": datetime.now(timezone.utc).isoformat(),
               "expected_observations": len(rows), "database_observations": len(actual_rows),
               "extra_observations": len(extra),
               "retained_history_observations": len(extra) if allow_retained_history else 0,
               "inventory_policy": "upsert_preserves_previous_history" if allow_retained_history else "exact_capture_inventory",
               "missing": missing[:10], "extra": extra[:10], "mismatched": mismatched[:10],
               "duplicate": duplicate[:10], "invalid_values_or_geography": invalid[:10],
               "pass": not (missing or (extra and not allow_retained_history) or mismatched or duplicate or invalid)}
    path = ROOT / "data/current" / code.lower() / f"{updater.source_scope}_database_acceptance.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    if not receipt["pass"]:
        raise ValueError(f"{code} database acceptance differs: {receipt}")
    return receipt


async def create_foreground_import_task(code, scope):
    """Serialize the active-task check and insert with dashboard enqueues."""
    async with get_db() as db:
        await ensure_task_type_enum_schema(db)
        # Match CrawlTaskService's NO KEY UPDATE lock. Keep it until this
        # RUNNING task is committed, so competing enqueues can see the task.
        country_id = (await db.execute(select(Country.id).where(
            Country.code == code).with_for_update(key_share=True))).scalar_one()
        active = (await db.execute(select(Task.task_uuid).where(
            Task.country_id == country_id, Task.task_type == TaskType.CRAWL_DATA,
            Task.status.in_([TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RETRYING])
        ))).scalars().all()
        if active:
            raise ValueError(f"{code} already has active crawl tasks: {active}")
        task = Task(
            task_type=TaskType.CRAWL_DATA, task_name=f"{code} {scope} full history import",
            country_id=country_id, status=TaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            input_data={"country_code": code, "source": scope, "process": True,
                        "manual_trigger": True, "mapping_notifications_enabled": False},
            tags=["remaining-surveillance", "foreground", "full-source-history"])
        db.add(task)
        await db.flush()
    return task


async def fail_foreground_import(task_uuid, lease, error):
    """Fence failure cleanup even after release, or if the initial claim failed."""
    async with get_db() as db:
        saved = (await db.execute(select(Task).where(
            Task.task_uuid == task_uuid).with_for_update())).scalar_one_or_none()
        if saved is None or saved.status != TaskStatus.RUNNING:
            return False
        metadata = dict(saved.metadata_ or {})
        ownership = dict(metadata.get("task_lease") or {})
        owner = ownership.get("owner")
        # Released leases retain their owner. Never alter a replacement owner;
        # an unclaimed task is ours only if __aenter__ never yielded a lease.
        if (lease is None and owner) or (lease is not None and owner != lease.owner):
            return False
        now = datetime.now(timezone.utc)
        message = str(error) or type(error).__name__
        saved.status = TaskStatus.FAILED
        saved.last_error = message
        saved.completed_at = now
        if ownership:
            ownership.update(released_at=now.isoformat(), terminal_status=TaskStatus.FAILED.value)
            metadata["task_lease"] = ownership
            saved.metadata_ = metadata
        for run in (await db.execute(select(CrawlRun).where(
            CrawlRun.raw_dir.like(f"%/{task_uuid}/%"), CrawlRun.status == "running"))).scalars():
            run.status = "failed"
            run.finished_at = now
            run.error_message = message
    return True


async def import_country(code, *, live=False, source=None):
    contract = source_contract(code, source)
    scope = contract["scope"]
    module = "who_annual" if scope == "who_gho_annual" else "spf_overseas"
    updater_name = "WHOAnnualUpdater" if module == "who_annual" else "SPFOverseasUpdater"
    module = contract.get("module", module)
    updater_name = contract.get("updater", updater_name)
    updater = CrawlService._make_updater(updater_name, code)
    connector = import_module(f"src.services.ingestion.countries.{module}")
    if not live:
        connector = CapturedReplay(connector, manifest_for(code, scope))
    task = await create_foreground_import_task(code, scope)
    lease = None
    try:
        print(json.dumps({"country_code": code, "task_uuid": task.task_uuid, "status": "running"}), flush=True)
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(
                CrawlService(), task=task, updater=updater, connector=connector,
                source=scope, force=False, process=True,
                connector_options={"country_code": code})
            from src.domain.collection import SourceArtifact
            async with get_db() as db:
                artifacts = (await db.execute(select(SourceArtifact).where(
                    SourceArtifact.task_uuid == task.task_uuid).order_by(SourceArtifact.id))).scalars().all()
            fetched = await asyncio.to_thread(connector.organize, artifacts, updater=updater,
                                             source=scope, country_code=code)
            acceptance = await verify_rows(code, updater, fetched.rows)
            output = {"country_code": code, "source": scope, "task_uuid": task.task_uuid,
                      "status": "completed", **asdict(result), "source_audit": fetched.source_audit,
                      "database_acceptance": acceptance}
            async with get_db() as db:
                saved = await lease.owned_task(db)
                saved.status = TaskStatus.COMPLETED
                saved.progress = 100
                saved.completed_at = datetime.now(timezone.utc)
                saved.output_data = output
        path = ROOT / "data/current" / code.lower() / f"{scope}_database_import_receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"country_code": code, "task_uuid": task.task_uuid,
                          "status": "completed", "observations": result.total_records}), flush=True)
        return output
    except BaseException as exc:
        await fail_foreground_import(task.task_uuid, lease, exc)
        raise


async def execute(args):
    try:
        if not args.apply:
            print(json.dumps({"mode": "preview", "countries": args.countries, "import": args.run_import}))
            return
        await bootstrap(args.countries)
        if args.run_import:
            semaphore = asyncio.Semaphore(args.concurrency)
            async def one(code):
                async with semaphore:
                    try:
                        return await import_country(code, live=args.live, source=args.source)
                    except Exception as exc:
                        print(json.dumps({"country_code": code, "status": "failed", "error": str(exc)}), flush=True)
                        return {"country_code": code, "status": "failed", "error": str(exc)}
            results = await asyncio.gather(*(one(code) for code in args.countries))
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
            if any(result["status"] != "completed" for result in results):
                raise SystemExit("Some country imports failed; inspect the receipt")
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+", choices=list(REMAINING_COUNTRIES), default=list(REMAINING_COUNTRIES))
    parser.add_argument("--source", help="Specific additional source; default is each country baseline")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--import", dest="run_import", action="store_true")
    parser.add_argument("--live", action="store_true", help="Fetch current source bytes rather than verified captured history")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--receipt", type=Path, default=ROOT / "reports/onboarding/remaining_surveillance_import.json")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
