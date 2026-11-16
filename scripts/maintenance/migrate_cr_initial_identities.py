#!/usr/bin/env python3
"""Reconcile only the first Costa Rica import's proven duplicate identities.

Preview is the default. --apply backs up every affected row before deleting
obsolete duplicate observations. Original PDFs, failed tasks and the immutable
revision ledger are preserved. Re-running after success validates a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, select, text
from scripts.onboard_remaining_surveillance import row_key
from src.core.database import dispose_database, get_db
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.domain import Task
from src.domain.collection import SourceArtifact
from src.domain.disease_ontology import (DiseaseSeriesObservation as Observation,
    DiseaseSeriesObservationRevision as Revision, DiseaseSurveillanceSeries as Series)
from src.domain.disease_mapping_registry import SourceDiseaseCategory as Category, DiseaseMappingAssertion as Assertion
from src.domain.public_series import PublicDiseaseSeries
from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater
from src.services.ingestion.countries import cr_notifications
from src.services.ingestion.artifacts import read_artifact

SOURCE = 'SRC_CR_MOH_NOTIFICATIONS'
OLD_TASK = 'b1963dfe-4d46-4485-a06f-ff3544a2f1bb'
NEW_TASK = '897ead43-3d88-4c24-b5fa-331df0109d5f'
DEFAULT_AUDIT = ROOT / 'reports/onboarding/cr_identity_migration_audit.json'
DEFAULT_RECEIPT = ROOT / 'reports/onboarding/cr_identity_migration_receipt.json'


def snapshot(row):
    return {prop.key: getattr(row, prop.key) for prop in inspect(type(row)).column_attrs}


def encode(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def durable_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, ensure_ascii=False, indent=2, default=encode) + '\n').encode()
    # A fresh name is mandatory: never replace the only pre-migration backup.
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(payload).hexdigest()


async def revision_state(db):
    rows = list((await db.execute(select(Revision).join(Series, Revision.series_code == Series.series_code).where(
        Series.country_code == 'CR', Series.source_system == SOURCE).order_by(Revision.id))).scalars())
    payload = json.dumps([snapshot(row) for row in rows], sort_keys=True, default=encode).encode()
    return rows, {'count': len(rows), 'ids': [row.id for row in rows], 'sha256': hashlib.sha256(payload).hexdigest()}


def deleted_fact_revision_evidence(observations, revision_rows):
    """Prove each complete deleted state survives in the immutable ledger."""
    fields = ('series_code', 'geography_key', 'dimension_key', 'dimensions',
              'value', 'unit', 'suppressed', 'suppression_reason',
              'quality_status', 'raw_data', 'metadata_')
    evidence = []
    for old in observations:
        old_time = old['time'].isoformat() if isinstance(old['time'], datetime) else old['time']
        matches = [revision.id for revision in revision_rows
                   if revision.time.isoformat() == old_time
                   and all(getattr(revision, field) == old[field] for field in fields)]
        if not matches:
            raise ValueError(f"Deleted fact {old['id']} is missing from the preserved revision ledger")
        evidence.append({'deleted_observation_id': old['id'], 'revision_ids': matches})
    return evidence


def prior_backup(receipt_path, *, old_ids, audit_sha256):
    """An absent row is not proof that this migration previously succeeded."""
    if not receipt_path.is_file():
        raise ValueError('Old rows are absent but the original migration receipt is missing')
    previous = json.loads(receipt_path.read_text())
    if (previous.get('migration') != 'cr_initial_identity_normalization_v1'
            or previous.get('old_task_uuid') != OLD_TASK
            or previous.get('new_task_uuid') != NEW_TASK
            or previous.get('audit_sha256') != audit_sha256
            or previous.get('apply') is not True or previous.get('pass') is not True):
        raise ValueError('Original migration receipt differs from this authorized audit')
    backup_bytes = Path(previous['backup_path']).read_bytes()
    if hashlib.sha256(backup_bytes).hexdigest() != previous['backup_sha256']:
        raise ValueError('Original backup integrity check failed')
    rows = json.loads(backup_bytes)['observations']
    if len(rows) != len(old_ids) or {row['id'] for row in rows} != old_ids:
        raise ValueError('Original backup does not preserve the exact authorized old observations')
    return previous, rows


async def migrate(args):
    document = json.loads(args.audit.read_text())
    evidence = document['matches']
    if (document['old_task_uuid'] != OLD_TASK or document['new_task_uuid'] != NEW_TASK
            or not document['all_equivalent'] or len(evidence) != 64
            or len({row['old_observation_id'] for row in evidence}) != 64
            or len({row['new_observation_id'] for row in evidence}) != 64):
        raise ValueError('Audit does not describe the authorized 64 original duplicates')
    for item in evidence:
        if item['matches'] != 1 or not all(item[field] for field in (
                'same_value', 'same_pdf_hash', 'same_url', 'old_task_has_same_original', 'new_task_has_same_original')):
            raise ValueError('Audit has a non-equivalent duplicate')
    receipt = {'migration': 'cr_initial_identity_normalization_v1', 'apply': args.apply,
               'checked_at': datetime.now(timezone.utc).isoformat(),
               'old_task_uuid': OLD_TASK, 'new_task_uuid': NEW_TASK,
               'audit_sha256': hashlib.sha256(args.audit.read_bytes()).hexdigest(),
               'revision_ledger_preserved': True, 'original_artifacts_preserved': True,
               'failed_task_history_preserved': True}
    async with get_db() as db:
        if args.apply:
            await acquire_country_data_mutation_lock(db, 'CR')
        else:
            await db.execute(text('SET TRANSACTION READ ONLY'))
        tasks = {task.task_uuid: task for task in (await db.execute(select(Task).where(
            Task.task_uuid.in_([OLD_TASK, NEW_TASK])))).scalars()}
        if len(tasks) != 2:
            raise ValueError('Expected source tasks are absent')
        for task in tasks.values():
            if (task.input_data.get('country_code') != 'CR'
                    or task.input_data.get('source') != 'cr_moh_notifications'):
                raise ValueError('Source task contract differs')
        artifacts = list((await db.execute(select(SourceArtifact).where(
            SourceArtifact.task_uuid.in_([OLD_TASK, NEW_TASK])).order_by(SourceArtifact.id))).scalars())
        originals = {}
        for artifact in artifacts:
            read_artifact(artifact)
            originals.setdefault(artifact.task_uuid, set()).add((artifact.source_url, artifact.sha256))
        new_artifacts = [a for a in artifacts if a.task_uuid == NEW_TASK]
        if len(new_artifacts) != 422 or len({a.attempt for a in new_artifacts}) != 1:
            raise ValueError('Authorized full import must contain exactly 422 artifacts from one attempt')
        fetched = await asyncio.to_thread(cr_notifications.organize, new_artifacts,
                                         updater=CostaRicaNotificationsUpdater(), source='cr_moh_notifications')
        expected = {row_key(row): row for row in fetched.rows}
        if len(expected) != 150 or len(fetched.rows) != 150:
            raise ValueError('Authorized capture no longer replays to 150 unique facts')
        statement = select(Observation).join(Series, Observation.series_code == Series.series_code).where(
            Series.country_code == 'CR', Series.source_system == SOURCE).order_by(Observation.id)
        if args.apply:
            statement = statement.with_for_update(of=Observation)
        rows = list((await db.execute(statement)).scalars())
        revision_rows, revisions_before = await revision_state(db)
        by_id = {row.id: row for row in rows}
        old_ids = {item['old_observation_id'] for item in evidence}
        present = old_ids & set(by_id)
        if present and present != old_ids:
            raise ValueError('Partial deletion detected; refuse to infer remaining migration scope')
        if present:
            deleted_rows = [snapshot(by_id[identity]) for identity in sorted(old_ids)]
        else:
            _, deleted_rows = prior_backup(args.receipt, old_ids=old_ids,
                                          audit_sha256=receipt['audit_sha256'])
        # A stable ledger hash alone cannot prove it contains the facts being
        # removed. Check every state before any deletion, including first apply.
        receipt['deleted_fact_revision_evidence'] = deleted_fact_revision_evidence(deleted_rows, revision_rows)
        canonical = [row for row in rows if row.id not in old_ids]
        actual = {row_key(row.raw_data): row for row in canonical}
        if len(actual) != len(canonical) or set(actual) != set(expected):
            raise ValueError('Canonical stored observations differ from full captured source identities')
        for key, raw in expected.items():
            stored = actual[key]
            if (any(stored.raw_data.get(field) != value for field, value in raw.items())
                    or stored.value != float(raw['Cases']) or stored.time.date().isoformat() != raw['Date']
                    or stored.geography_key != raw['GeographyKey']):
                raise ValueError(f'Canonical source fact changed: {key}')
        obsolete_codes = set()
        for item in evidence:
            current = by_id.get(item['new_observation_id'])
            if current is None or current.id not in {r.id for r in canonical}:
                raise ValueError('Replacement observation is absent')
            if item['old_source_code'] != item['new_source_code']:
                obsolete_codes.add(item['old_source_code'])
            if not present:
                continue
            previous = by_id[item['old_observation_id']]
            before, after = previous.raw_data, current.raw_data
            if (previous.series_code != item['old_series_code'] or previous.dimension_key != item['old_dimension_key']
                    or current.series_code != item['new_series_code'] or current.dimension_key != item['new_dimension_key']
                    or before.get('SourceDiseaseCode') != item['old_source_code']
                    or after.get('SourceDiseaseCode') != item['new_source_code']):
                raise ValueError('Old or replacement identity differs from reviewed audit')
            if (not tasks[OLD_TASK].started_at <= previous.created_at <= tasks[OLD_TASK].completed_at
                    or current.created_at < tasks[NEW_TASK].started_at):
                raise ValueError('Observation timestamps are outside the authorized task provenance')
            for field in ('Date', 'RawDiseaseLabel', 'Cases', 'SourceURL', 'RawSHA256'):
                if before.get(field) != after.get(field):
                    raise ValueError(f'Duplicate source fact differs in {field}')
            pair = (before['SourceURL'], before['RawSHA256'])
            if pair not in originals[OLD_TASK] or pair not in originals[NEW_TASK]:
                raise ValueError('Equivalent fact lacks immutable evidence in both source tasks')
            differences = {field for field in set(before) | set(after) if before.get(field) != after.get(field)}
            if differences - {'Dimensions', 'SourceDiseaseCode'}:
                raise ValueError('Unexpected old observation difference')
            dimensions = json.loads(before['Dimensions'])
            if dimensions.pop('source_preliminary', None) is not ('*' in before['RawDiseaseLabel']):
                raise ValueError('Old preliminary marker differs from the original source label')
            dimensions['source_label'] = ' '.join(dimensions['source_label'].replace('*', '').split())
            if dimensions != json.loads(after['Dimensions']):
                raise ValueError('Only the preliminary marker and label footnotes may change dimension identity')
            if after['SourceDiseaseCode'] != 'annual_' + hashlib.sha256(dimensions['source_label'].encode()).hexdigest()[:20]:
                raise ValueError('Replacement code is not the stable unfootnoted source label identity')
        if len(obsolete_codes) != 5:
            raise ValueError('Expected exactly five obsolete provisional-label categories')
        series_statement = select(Series).where(Series.country_code == 'CR', Series.source_system == SOURCE,
                                                Series.source_series_code.in_(obsolete_codes))
        categories_statement = select(Category).where(Category.country_code == 'CR', Category.source_id == SOURCE,
                                                      Category.source_code.in_(obsolete_codes))
        if args.apply:
            series_statement = series_statement.with_for_update()
            categories_statement = categories_statement.with_for_update()
        obsolete_series = list((await db.execute(series_statement)).scalars())
        obsolete_categories = list((await db.execute(categories_statement)).scalars())
        if len(obsolete_series) != 5 or len(obsolete_categories) != 5:
            raise ValueError('Obsolete series/category scope differs from the five reviewed categories')
        category_ids = [row.id for row in obsolete_categories]
        assertions = list((await db.execute(select(Assertion).where(Assertion.category_id.in_(category_ids)))).scalars())
        if assertions:
            raise ValueError('Obsolete categories have mapping assertions; explicit review is required')
        public = list((await db.execute(select(PublicDiseaseSeries).where(PublicDiseaseSeries.country_code == 'CR'))).scalars())
        if public:
            raise ValueError('Costa Rica already has public series; explicit publication reconciliation is required')
        for series in obsolete_series:
            if (not (series.metadata_ or {}).get('dynamic_holding') or series.disease_id is not None
                    or any(row.series_code == series.series_code for row in canonical)):
                raise ValueError('Obsolete series has mapped or canonical observations')
        receipt.update(canonical_observations=len(canonical), observations_to_remove=len(present),
                       obsolete_series=[row.series_code for row in obsolete_series],
                       obsolete_category_ids=category_ids, mapping_assertions=0, public_series=0)
        if args.apply and present:
            backup = args.receipt.parent / f'cr_initial_identities_backup_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json'
            affected_series = {item['old_series_code'] for item in evidence}
            all_series = list((await db.execute(select(Series).where(Series.series_code.in_(affected_series)))).scalars())
            backup_document = {**receipt, 'observations': deleted_rows,
                               'series': [snapshot(row) for row in all_series],
                               'categories': [snapshot(row) for row in obsolete_categories],
                               'equivalence_evidence': evidence}
            receipt.update(backup_path=str(backup), backup_sha256=durable_json(backup, backup_document))
            for identity in sorted(old_ids):
                await db.delete(by_id[identity])
            await db.flush()
            for series in obsolete_series:
                replacement = next(item for item in evidence if item['old_series_code'] == series.series_code)
                series.is_active = False
                series.availability_status = 'discontinued'
                series.metadata_ = {**series.metadata_, 'retired_by': receipt['migration'],
                                    'superseded_by_series_code': replacement['new_series_code'],
                                    'retirement_note': 'Obsolete initial category; canonical equivalent retained with provisional marker outside identity.'}
            for category in obsolete_categories:
                replacement = next(item for item in evidence if item['old_source_code'] == category.source_code)
                replacement_category = (await db.execute(select(Category).where(
                    Category.country_code == 'CR', Category.source_id == SOURCE,
                    Category.source_code == replacement['new_source_code'],
                    Category.definition_version == category.definition_version))).scalar_one()
                category.is_active = False
                category.status = 'retired'
                category.ai_status = 'not_required'
                category.metadata_ = {**(category.metadata_ or {}), 'retired_by': receipt['migration'],
                                      'superseded_by_category_id': replacement_category.id}
        elif args.apply:
            if any(series.is_active for series in obsolete_series) or any(category.is_active for category in obsolete_categories):
                raise ValueError('Old rows are absent but retirement state is inconsistent')
            receipt['idempotent_noop'] = True
        await db.flush()
        revision_rows, revisions_after = await revision_state(db)
        if revisions_before != revisions_after:
            raise ValueError('Revision ledger IDs or contents changed during reconciliation')
        receipt['revision_ledger_check'] = {'before': revisions_before, 'after': revisions_after, 'pass': True}
        receipt['deleted_fact_revision_evidence'] = deleted_fact_revision_evidence(deleted_rows, revision_rows)
        receipt['pass'] = True
    # The transaction commits before the applied receipt is published.
    if args.apply:
        if args.receipt.exists() and receipt.get('idempotent_noop'):
            previous = json.loads(args.receipt.read_text())
            previous['last_idempotence_check'] = receipt['checked_at']
            previous['idempotent_noop_confirmed'] = True
            previous['revision_ledger_check'] = receipt['revision_ledger_check']
            previous['deleted_fact_revision_evidence'] = receipt['deleted_fact_revision_evidence']
            receipt = previous
        temporary = args.receipt.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(args.receipt)
    print(json.dumps({key: receipt.get(key) for key in (
        'migration', 'apply', 'canonical_observations', 'observations_to_remove',
        'idempotent_noop_confirmed', 'backup_path', 'pass')}, ensure_ascii=False), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--audit', type=Path, default=DEFAULT_AUDIT)
    parser.add_argument('--receipt', type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    async def execute():
        try:
            await migrate(args)
        finally:
            await dispose_database()
    asyncio.run(execute())


if __name__ == '__main__':
    main()
