#!/usr/bin/env python3
"""Replay a verified official archive through collect/organize/store.

Default: inspect and organize offline. --apply registers immutable raw artifacts
and writes source-native facts through the ordinary staged ingestion runner.
It does not publish a website or invent disease mappings.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib import import_module
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import suppress

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
COUNTRIES = {
    'CL': ('ChileENOHistoricalUpdater', 'cl_eno_historical'),
    'CO': ('ColombiaINSSivigilaUpdater', 'co_ins_sivigila_historical'),
    'MW': ('MalawiPHIMIDSRUpdater', 'mw_phim_idsr_weekly'),
    'ZA': ('SouthAfricaNICDNMCUpdater', 'za_nicd_nmc'),
    'ID': ('IndonesiaKemkesProfileUpdater', 'id_kemkes_profile'),
    'MX': ('MexicoDGEAnnualUpdater', 'mx_dge_annual'),
    'NG': ('NigeriaNCDCUpdater', 'ncdc_surveillance'),
    'KH': ('CambodiaCamEWARNWeeklyUpdater', 'kh_camewarn_weekly'),
    'AR': ('ArgentinaBENUpdater', 'ar_ben_weekly'),
    'PE': ('PeruCDCWeeklyUpdater', 'pe_cdc_weekly'),
}


def load_archive_manifest(path: Path, country: str):
    """Validate the complete manifest before any database mutation."""
    path = path.resolve()
    raw_root = (ROOT / 'data/raw' / country.lower()).resolve()
    if not path.is_relative_to(raw_root):
        raise ValueError(f'Archive manifest must be under {raw_root}')
    document = json.loads(path.read_bytes())
    records = document['artifacts'] if isinstance(document, dict) else document
    if not isinstance(records, list) or not records:
        raise ValueError('Nonempty original artifact manifest required')
    artifacts = []
    for record in records:
        original = (ROOT / record['relative_path']).resolve()
        if not original.is_relative_to(raw_root):
            raise ValueError('Archive artifact escapes its country raw directory')
        payload = original.read_bytes()
        if len(payload) != record['byte_size'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
            raise ValueError(f'Archive integrity check failed: {original}')
        if not str(record['source_url']).startswith(('https://', 'http://')):
            raise ValueError('Original official source URL required')
        artifacts.append(SimpleNamespace(
            relative_path=original.relative_to(ROOT).as_posix(),
            sha256=record['sha256'], byte_size=record['byte_size'],
            source_url=record['source_url'], kind=record['kind'],
            metadata_=record.get('metadata_', {}),
        ))
    return artifacts


class ArchiveReplayConnector:
    def __init__(self, artifacts, delegate):
        self.artifacts = artifacts
        self.delegate = delegate

    async def collect(self, writer, **options):
        from src.services.ingestion.artifacts import ArtifactWriter, read_artifact

        async def register(artifact):
            # Independent writers retain the normal immutable-file/DB commit
            # contract while allowing bounded registration of verified bytes.
            local = ArtifactWriter(writer.task_uuid, writer.attempt,
                writer.directory.parent.parent.name)
            payload = await asyncio.to_thread(read_artifact, artifact)
            return await local.write(payload, source_url=artifact.source_url,
                kind=artifact.kind, metadata=artifact.metadata_)

        for offset in range(0, len(self.artifacts), 4):
            results = await asyncio.gather(*(
                register(artifact) for artifact in self.artifacts[offset:offset + 4]
            ), return_exceptions=True)
            writer.artifacts.extend(item for item in results if not isinstance(item, BaseException))
            failures = [item for item in results if isinstance(item, BaseException)]
            if failures:
                raise failures[0]
            index = min(offset + 4, len(self.artifacts))
            if index % 100 == 0 or index == len(self.artifacts):
                print(json.dumps({'stage': 'collect', 'registered_artifacts': index,
                    'total_artifacts': len(self.artifacts)}), flush=True)
        return writer.artifacts

    def organize(self, artifacts, **options):
        return self.delegate.organize(artifacts, **options)


async def maintain_lease(task_uuid, owner):
    from src.core.task_manager import task_manager
    while True:
        await asyncio.sleep(30)
        if not await task_manager.heartbeat_task_lease(task_uuid, owner):
            return


async def import_archive(country, manifest, *, apply=False, start_year=None, receipt_path=None):
    from src.core.database import get_db, dispose_database
    from src.core.country_library import get_country_bootstrap_config, get_country_profile
    from src.core.db_schema import ensure_country_scope_for_code
    from src.domain import Country, CrawlRun, RunStage, Task, TaskStatus, TaskType
    from src.services.crawl_service import CrawlService
    from src.services.ingestion.runner import run_surveillance
    from sqlalchemy import select
    country = country.upper()
    updater_name, source = COUNTRIES[country]
    updater = getattr(import_module(f'src.data.processors.{country.lower()}'), updater_name)()
    connector = import_module(f'src.services.ingestion.countries.{country.lower()}')
    artifacts = await asyncio.to_thread(load_archive_manifest, Path(manifest), country)
    options = {'start_year': int(start_year or updater.full_history_start_year)}
    if country == 'KH':
        options['parse_cache_directory'] = ROOT / 'data/cache/kh/camewarn-parses'
    elif country == 'PE':
        options['parse_cache_directory'] = ROOT / 'data/cache/pe/bulletin-parses'
    elif country == 'ID':
        options['parse_cache_directory'] = ROOT / 'data/cache/id/kemkes-parses'
    receipt_path = Path(receipt_path) if receipt_path else ROOT / 'data/current' / country.lower() / 'database_import_receipt.json'
    try:
        if not apply:
            fetched = await asyncio.to_thread(connector.organize, artifacts, updater=updater, source=source, **options)
            result = {'mode': 'offline_preview', 'country': country, 'artifacts': len(artifacts),
                'rows': len(fetched.rows), 'source_audit': getattr(fetched, 'source_audit', {}),
                'source_manifest': str(Path(manifest).resolve())}
            return result
        task_uuid = str(uuid4())
        started = datetime.now(timezone.utc)
        owner = f'archive-replay:{task_uuid}'
        async with get_db() as db:
            row = (await db.execute(select(Country).where(Country.code == country))).scalar_one_or_none()
            if row is None:
                profile = get_country_profile(country)
                config = get_country_bootstrap_config(country)
                row = Country(code=country, name=profile.name, name_en=profile.name_en,
                    name_local=profile.name_local, language=profile.language, timezone=profile.timezone)
                for key in ['data_source_url', 'data_source_type', 'crawler_config', 'parser_config', 'disease_mapping_rules', 'report_config', 'notes']:
                    setattr(row, key, config[key])
                db.add(row)
                await db.flush()
            await ensure_country_scope_for_code(db, country)
            task = Task(task_uuid=task_uuid, task_type=TaskType.CRAWL_DATA,
                task_name=f'{country} full official historical archive import', country_id=row.id,
                status=TaskStatus.RUNNING, started_at=started,
                input_data={'country': country, 'source': source, 'start_year': options['start_year'],
                    'archive_manifest': str(Path(manifest).resolve()), 'collection_mode': 'verified_archive_replay'},
                metadata_={'origin': 'operator_authorized_backfill', 'public_release_enabled': False,
                    'task_lease': {'owner': owner, 'heartbeat_at': started.isoformat()}})
            db.add(task)
        heartbeat = asyncio.create_task(maintain_lease(task_uuid, owner))
        try:
            result = await run_surveillance(CrawlService(), task=task, updater=updater,
                connector=ArchiveReplayConnector(artifacts, connector), source=source,
                force=True, process=True, connector_options=options)
            async with get_db() as db:
                saved = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one()
                saved.status = TaskStatus.COMPLETED
                saved.completed_at = datetime.now(timezone.utc)
                saved.progress = 100
                saved.output_data = {'records': result.total_records, 'crawl_run_id': result.crawl_run_id,
                    'artifact_count': len(artifacts), 'source_complete': False,
                    'note': 'Imported validated archive facts; source coverage gaps remain in organize stage audit.'}
                stage = (await db.execute(select(RunStage).where(
                    RunStage.task_uuid == task_uuid, RunStage.stage == 'organize'
                ).order_by(RunStage.attempt.desc()).limit(1))).scalar_one()
                source_audit = dict(stage.metrics or {}).get('source_audit', {})
            receipt = {'mode': 'database_imported', 'country': country, 'source': source,
                'task_uuid': task_uuid, 'records': result.total_records, 'crawl_run_id': result.crawl_run_id,
                'artifacts': len(artifacts), 'source_manifest': str(Path(manifest).resolve()),
                'completed_at': datetime.now(timezone.utc).isoformat(), 'public_release_enabled': False,
                'source_audit': source_audit,
                'coverage_note': 'See source_audit for unparsed, unavailable, chart-only and conflicting observations.'}
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = receipt_path.with_suffix(receipt_path.suffix + '.tmp')
            temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            temporary.replace(receipt_path)
            return receipt
        except BaseException as exc:
            async with get_db() as db:
                saved = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one()
                saved.status = TaskStatus.FAILED
                saved.completed_at = datetime.now(timezone.utc)
                saved.last_error = str(exc)[:8000]
                runs = (await db.execute(select(CrawlRun).where(
                    CrawlRun.raw_dir.like(f'data/raw/{country.lower()}/{task_uuid}/%'),
                    CrawlRun.status == 'running'
                ))).scalars().all()
                for run in runs:
                    run.status = 'failed'
                    run.finished_at = datetime.now(timezone.utc)
                    run.error_message = str(exc) or type(exc).__name__
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country', choices=sorted(COUNTRIES), required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--start-year', type=int)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--receipt-path', type=Path)
    args = parser.parse_args()
    result = asyncio.run(import_archive(args.country, args.manifest, apply=args.apply,
                                        start_year=args.start_year, receipt_path=args.receipt_path))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
