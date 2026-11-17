#!/usr/bin/env python3
"""Import downloaded Thai Report 506 history through durable staged ingestion.

No network collection, country creation, task lifecycle notifications or public
release occurs here. Source bytes are re-archived with ArtifactWriter before
the normal offline organizer and source-series transaction run.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
import io
from types import SimpleNamespace
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core.database import dispose_database, get_db
from src.core.task_manager import task_manager
from src.data.crawlers.th import (
    CONTRACTS_BY_CODE, DEFAULT_SCOPE, INDEX_URL, discover_diseases,
    discover_monthly_report, validate_year_window,
)
from src.data.processors.th import ThailandDOE506MonthlyUpdater
from src.domain import Country, CrawlRun, Task
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import th
from src.services.ingestion.runner import run_surveillance
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.foreground import foreground_task_lease, ForegroundTaskLeaseLostError


class LocalHistoryConnector:
    """Replay an official-source download directory without using cached CSVs."""

    def __init__(self, raw_root):
        self.raw_root = Path(raw_root).resolve()
        self.last_audit = None
        self.download_audit = {}
        audit_path = self.raw_root / "audit.jsonl"
        if audit_path.exists():
            for line in audit_path.read_text().splitlines():
                row = json.loads(line)
                self.download_audit[(row["code"], int(row["year"]))] = row

    async def collect(self, writer, *, source, year):
        th.normalize_source(source)
        validate_year_window(year, year)
        index = (self.raw_root / "index.html").read_bytes()
        catalog = discover_diseases(index)
        entries = []

        def add(payload, *, source_url, kind, metadata, name):
            entries.append((name, payload, dict(name=name, source_url=source_url, kind=kind,
                            metadata_=metadata, byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest())))

        add(index, source_url=INDEX_URL, kind="html_report", name="index.html",
            metadata={"endpoint": "index", "replayed_from": str(self.raw_root / "index.html")})
        errors = []
        for code in CONTRACTS_BY_CODE:
            history_path = self.raw_root / f"{code}_history.html"
            if code not in catalog or not history_path.exists():
                errors.append({"disease_code": code, "year": year, "error": "Downloaded official disease history is missing"})
                continue
            payload = history_path.read_bytes()
            history_url = catalog[code] + "&dcontent=old"
            add(payload, source_url=history_url, kind="html_report", name=f"{code}_history.html",
                metadata={"endpoint": "history", "disease_code": code, "replayed_from": str(history_path)})
            report_path = self.raw_root / str(year) / f"{code}.rtf"
            try:
                report_url = discover_monthly_report(payload, disease_code=code, year=year)
            except ValueError as exc:
                errors.append({"disease_code": code, "year": year, "error": str(exc)})
                continue
            if not report_path.exists():
                original = self.download_audit.get((code, year), {})
                errors.append({"disease_code": code, "year": year, "url": report_url,
                               "error": original.get("error", "Downloaded monthly source bytes are missing")})
                continue
            add(report_path.read_bytes(), source_url=report_url, kind="monthly_rtf", name=f"{year}_{code}.rtf",
                metadata={"endpoint": "monthly", "disease_code": code, "year": year,
                          "replayed_from": str(report_path)})
        add(json.dumps({
            "expected": [[code, year] for code in CONTRACTS_BY_CODE],
            "catalog_category_count": len(catalog), "collection_errors": errors,
            "import_mode": "downloaded_official_history", "download_audit_root": str(self.raw_root),
        }, ensure_ascii=False, sort_keys=True).encode(), source_url=INDEX_URL,
            kind="archive_json", metadata={"endpoint": "collection_audit"}, name="collection_audit.json")
        manifest = json.dumps({"version": 1, "members": [entry[2] for entry in entries]},
                              ensure_ascii=False, sort_keys=True).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload, _ in entries:
                archive.writestr(name, payload)
            archive.writestr("manifest.json", manifest)
        await writer.write(stream.getvalue(), source_url=INDEX_URL, kind="th_history_bundle",
                           metadata={"endpoint": "history_bundle", "year": year,
                                     "member_count": len(entries),
                                     "manifest_sha256": hashlib.sha256(manifest).hexdigest()})
        return writer.artifacts

    def organize(self, artifacts, *, updater, source, year):
        if len(artifacts) != 1 or artifacts[0].kind != "th_history_bundle":
            raise ValueError("Expected exactly one durable Thailand history source bundle")
        with zipfile.ZipFile(io.BytesIO(read_artifact(artifacts[0]))) as archive:
            manifest_bytes = archive.read("manifest.json")
            if hashlib.sha256(manifest_bytes).hexdigest() != artifacts[0].metadata_["manifest_sha256"]:
                raise ValueError("Thailand source bundle manifest hash mismatch")
            manifest = json.loads(manifest_bytes)
            names = [member["name"] for member in manifest["members"]]
            if manifest["version"] != 1 or len(names) != len(set(names)) or set(archive.namelist()) != {*names, "manifest.json"}:
                raise ValueError("Thailand source bundle member inventory mismatch")
            unpacked = []
            for member in manifest["members"]:
                payload = archive.read(member["name"])
                if len(payload) != member["byte_size"] or hashlib.sha256(payload).hexdigest() != member["sha256"]:
                    raise ValueError("Thailand raw source bundle member hash mismatch")
                unpacked.append(SimpleNamespace(**member, payload=payload))
        fetched = th.organize(unpacked, updater=updater, source=source, start_year=year, end_year=year,
                              artifact_reader=lambda artifact: artifact.payload)
        self.last_audit = fetched.source_audit
        return fetched


async def import_year(raw_root, year, country_id):
    updater = ThailandDOE506MonthlyUpdater()
    updater.mapping_notifications_enabled = False
    connector = LocalHistoryConnector(raw_root)
    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name=f"Thailand Report 506 source history {year}",
        country_id=country_id, priority=TaskPriority.NORMAL, status=TaskStatus.RUNNING,
        input_data={"country_code": "TH", "source": DEFAULT_SCOPE, "start_year": year,
                    "end_year": year, "manual_trigger": True,
                    "downloaded_source_replay": True, "mapping_notifications_enabled": False},
        tags=["history-import", "th-doe506", "foreground"],
    )
    await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
    service = CrawlService()
    lease = None
    try:
        async with foreground_task_lease(task.task_uuid) as lease:
            result = await run_surveillance(
                service, task=task, updater=updater, connector=connector,
                source=DEFAULT_SCOPE, force=False, process=True,
                connector_options={"year": year},
            )
        output = {**asdict(result), "year": year, "source_audit": connector.last_audit}
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.output_data = output
            saved.status = TaskStatus.COMPLETED
            saved.progress = 100
            saved.completed_at = datetime.now(timezone.utc)
            if saved.started_at:
                saved.actual_duration = int((saved.completed_at - saved.started_at).total_seconds())
        return {"task_uuid": task.task_uuid, **output}
    except ForegroundTaskLeaseLostError:
        raise
    except BaseException as exc:
        if lease is None:
            raise
        async with get_db() as db:
            saved = await lease.owned_task(db)
            saved.status = TaskStatus.FAILED
            saved.completed_at = datetime.now(timezone.utc)
            saved.last_error = str(exc)
            saved.retry_count = int(saved.retry_count or 0) + 1
            runs = (await db.execute(select(CrawlRun).where(
                CrawlRun.country_code == "TH", CrawlRun.status == "running",
                CrawlRun.raw_dir.like(f"%/{task.task_uuid}/%"),
            ))).scalars().all()
            for run in runs:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.error_message = str(exc)
        raise


async def run(args):
    years = validate_year_window(args.start_year, args.end_year)
    async with get_db() as db:
        country_id = (await db.execute(select(Country.id).where(Country.code == "TH"))).scalar_one_or_none()
    if country_id is None:
        raise RuntimeError("TH country must be registered before importing history")
    results = []
    for year in reversed(years):
        result = await import_year(args.raw_root, year, country_id)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.result_json:
            Path(args.result_json).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data/raw/th/history_review_20261115")
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args()

    async def execute():
        try:
            await run(args)
        finally:
            await dispose_database()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
