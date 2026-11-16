"""Migration guards run against controlled ORM facts, never a real database."""
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.maintenance import migrate_cr_initial_identities as migration


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self.rows

    def scalar_one(self):
        assert len(self.rows) == 1
        return self.rows[0]


@pytest.fixture
def capture(tmp_path, monkeypatch):
    old_time = datetime(2026, 11, 15, 1, tzinfo=timezone.utc)
    new_time = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)
    observations, old_rows, canonical, revisions, audit = [], [], [], [], []
    obsolete_series, categories, replacement_categories = [], [], []
    when = datetime(2015, 1, 1, tzinfo=timezone.utc)
    for index in range(150):
        label = f'Category {index}' + ('*' if index < 5 else '')
        canonical_label = label.replace('*', '')
        source_code = 'annual_' + hashlib.sha256(canonical_label.encode()).hexdigest()[:20]
        dims = {'source_label': canonical_label, 'source_report_kind': 'individual_annual'}
        raw = {'Date': '2015-01-01', 'RawDiseaseLabel': label, 'Cases': str(100 + index),
               'SourceURL': 'https://www.ministeriodesalud.go.cr/fixture/file', 'RawSHA256': 'a' * 64,
               'SourceDiseaseCode': source_code, 'GeographyKey': 'country:CR:national',
               'DefinitionVersion': 'cr_moh_individual_annual_v1', 'Measure': 'case_notifications',
               'ReportingBasis': 'national_individual_notifications', 'Frequency': 'annual',
               'Dimensions': json.dumps(dims, sort_keys=True)}
        current = migration.Observation(id=1001 + index, time=when, series_code='HOLD_' + source_code,
            dimension_key='new_' + str(index), geography_key=raw['GeographyKey'], dimensions=dims,
            value=float(raw['Cases']), unit='count', suppressed=False, suppression_reason=None,
            quality_status='raw', raw_data=raw, metadata_={}, created_at=new_time, updated_at=new_time)
        observations.append(current); canonical.append(raw)
        if index >= 64:
            continue
        old_code = 'annual_' + hashlib.sha256(label.encode()).hexdigest()[:20]
        old_dims = {**dims, 'source_label': label, 'source_preliminary': '*' in label}
        old_raw = {**raw, 'SourceDiseaseCode': old_code, 'Dimensions': json.dumps(old_dims, sort_keys=True)}
        old = migration.Observation(id=index + 1, time=when, series_code='HOLD_' + old_code,
            dimension_key='old_' + str(index), geography_key=raw['GeographyKey'], dimensions=old_dims,
            value=float(raw['Cases']), unit='count', suppressed=False, suppression_reason=None,
            quality_status='raw', raw_data=old_raw, metadata_={}, created_at=old_time, updated_at=old_time)
        observations.append(old); old_rows.append(old)
        revisions.append(migration.Revision(id=index + 1, time=when, series_code=old.series_code,
            geography_key=old.geography_key, dimension_key=old.dimension_key, dimensions=copy.deepcopy(old_dims),
            value=old.value, unit='count', suppressed=False, suppression_reason=None, quality_status='raw',
            raw_data=copy.deepcopy(old_raw), metadata_={}, recorded_at=old_time))
        audit.append({'old_observation_id': old.id, 'new_observation_id': current.id,
            'old_series_code': old.series_code, 'new_series_code': current.series_code,
            'old_dimension_key': old.dimension_key, 'new_dimension_key': current.dimension_key,
            'old_source_code': old_code, 'new_source_code': source_code, 'matches': 1,
            **{key: True for key in ('same_value', 'same_pdf_hash', 'same_url',
                                    'old_task_has_same_original', 'new_task_has_same_original')}})
        if index < 5:
            obsolete_series.append(migration.Series(id=index + 1, series_code=old.series_code,
                country_code='CR', source_system=migration.SOURCE, source_series_code=old_code,
                disease_id=None, metadata_={'dynamic_holding': True}, is_active=True))
            categories.append(migration.Category(id=index + 1, country_code='CR', source_id=migration.SOURCE,
                source_code=old_code, definition_version=raw['DefinitionVersion'], is_active=True, metadata_={}))
            replacement_categories.append(SimpleNamespace(id=101 + index))
    pair = (canonical[0]['SourceURL'], canonical[0]['RawSHA256'])
    artifacts = [SimpleNamespace(task_uuid=migration.NEW_TASK, attempt=1, source_url=pair[0], sha256=pair[1]) for _ in range(422)]
    artifacts.append(SimpleNamespace(task_uuid=migration.OLD_TASK, attempt=1, source_url=pair[0], sha256=pair[1]))
    tasks = [SimpleNamespace(task_uuid=task, input_data={'country_code': 'CR', 'source': 'cr_moh_notifications'},
        started_at=stamp, completed_at=stamp) for task, stamp in ((migration.OLD_TASK, old_time), (migration.NEW_TASK, new_time))]
    state = SimpleNamespace(observations=observations, old_rows=old_rows, revisions=revisions,
        canonical=canonical, obsolete_series=obsolete_series, categories=categories,
        assertions=[], public=[], deleted=[], category_cursor=0)

    async def execute(statement):
        if str(statement) == 'SET TRANSACTION READ ONLY':
            return Result([])
        entity = statement.column_descriptions[0]['entity']
        if entity is migration.Task:
            return Result(tasks)
        if entity is migration.SourceArtifact:
            return Result(artifacts)
        if entity is migration.Observation:
            # Assert the production query retains both database scope fences.
            sql = str(statement)
            params = statement.compile().params
            assert 'country_code' in sql and 'source_system' in sql
            assert 'CR' in params.values() and migration.SOURCE in params.values()
            return Result(state.observations)
        if entity is migration.Revision:
            return Result(state.revisions)
        if entity is migration.Series:
            return Result(state.obsolete_series)
        if entity is migration.Category:
            if 'source_code IN' in str(statement):
                return Result(state.categories)
            row = replacement_categories[state.category_cursor % 5]; state.category_cursor += 1
            return Result([row])
        if entity is migration.Assertion:
            return Result(state.assertions)
        if entity is migration.PublicDiseaseSeries:
            return Result(state.public)
        pytest.fail(f'Unexpected query: {statement}')

    async def delete(row):
        state.deleted.append(row.id)
        state.observations.remove(row)

    db = SimpleNamespace(execute=execute, delete=delete, flush=AsyncMock())

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(migration, 'get_db', database)
    monkeypatch.setattr(migration, 'acquire_country_data_mutation_lock', AsyncMock())
    monkeypatch.setattr(migration, 'read_artifact', lambda _: b'offline verified fixture')
    monkeypatch.setattr(migration.cr_notifications, 'organize', lambda *a, **kw: SimpleNamespace(rows=canonical))
    args = SimpleNamespace(audit=tmp_path / 'audit.json', receipt=tmp_path / 'receipt.json', apply=True)
    args.audit.write_text(json.dumps({'old_task_uuid': migration.OLD_TASK, 'new_task_uuid': migration.NEW_TASK,
                                      'all_equivalent': True, 'matches': audit}))
    return state, args


async def test_preview_proves_ledger_without_deleting_or_retiring(capture):
    state, args = capture
    args.apply = False
    receipt = await migration.migrate(args)
    assert receipt['pass'] and receipt['apply'] is False
    assert len(receipt['deleted_fact_revision_evidence']) == 64
    assert state.deleted == [] and not args.receipt.exists()
    assert all(row.is_active for row in state.obsolete_series + state.categories)


async def test_first_apply_proves_all_64_states_before_delete_and_repeat_is_noop(capture):
    state, args = capture
    receipt = await migration.migrate(args)
    assert len(state.deleted) == 64
    assert len(receipt['deleted_fact_revision_evidence']) == 64
    assert receipt['revision_ledger_check']['before'] == receipt['revision_ledger_check']['after']
    assert len(json.loads(Path(receipt['backup_path']).read_text())['observations']) == 64
    assert all(not row.is_active for row in state.obsolete_series + state.categories)
    repeated = await migration.migrate(args)
    assert repeated['idempotent_noop_confirmed'] is True
    assert len(state.deleted) == 64
    assert len(repeated['deleted_fact_revision_evidence']) == 64
    assert migration.CostaRicaNotificationsUpdater().public_release_enabled is False


@pytest.mark.parametrize('missing', ['all', 'one'])
async def test_first_apply_refuses_incomplete_ledger_before_delete_or_backup(capture, missing):
    state, args = capture
    state.revisions = [] if missing == 'all' else state.revisions[:-1]
    with pytest.raises(ValueError, match='missing from the preserved revision ledger'):
        await migration.migrate(args)
    assert state.deleted == [] and not args.receipt.exists()
    assert not list(args.receipt.parent.glob('cr_initial_identities_backup_*'))


@pytest.mark.parametrize('field,value', [
    ('geography_key', 'country:OTHER:national'), ('unit', 'rate'), ('value', 999),
    ('suppressed', True), ('dimensions', {'other': 'stratum'}), ('raw_data', {'changed': True}),
    ('metadata_', {'missing_value_status': 'source_missing'}),
])
async def test_matching_raw_label_is_insufficient_without_exact_ledger_state(capture, field, value):
    state, args = capture
    setattr(state.revisions[0], field, value)
    with pytest.raises(ValueError, match='missing from the preserved revision ledger'):
        await migration.migrate(args)
    assert state.deleted == []


async def test_absent_old_rows_without_original_receipt_fail_closed(capture):
    state, args = capture
    state.observations = [row for row in state.observations if row.id > 1000]
    for row in state.obsolete_series + state.categories:
        row.is_active = False
    with pytest.raises(ValueError, match='original migration receipt is missing'):
        await migration.migrate(args)
    assert state.deleted == [] and not args.receipt.exists()


async def test_repeat_still_requires_every_deleted_fact_in_revision_ledger(capture):
    state, args = capture
    await migration.migrate(args)
    state.revisions.pop()
    with pytest.raises(ValueError, match='missing from the preserved revision ledger'):
        await migration.migrate(args)
    assert len(state.deleted) == 64


@pytest.mark.parametrize('change', ['missing_receipt', 'tampered_backup', 'missing_backup_row', 'wrong_audit'])
async def test_repeat_requires_valid_receipt_and_complete_original_backup(capture, change):
    state, args = capture
    receipt = await migration.migrate(args)
    backup = Path(receipt['backup_path'])
    if change == 'missing_receipt':
        args.receipt.unlink()
    elif change == 'tampered_backup':
        backup.write_text('tampered')
    elif change == 'missing_backup_row':
        document = json.loads(backup.read_text()); document['observations'].pop()
        backup.write_text(json.dumps(document))
        receipt['backup_sha256'] = hashlib.sha256(backup.read_bytes()).hexdigest()
        args.receipt.write_text(json.dumps(receipt))
    else:
        receipt['audit_sha256'] = 'wrong'; args.receipt.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        await migration.migrate(args)
    assert len(state.deleted) == 64


@pytest.mark.parametrize('blocker', ['assertions', 'public'])
async def test_mapping_and_publication_guards_still_block_migration(capture, blocker):
    state, args = capture
    setattr(state, blocker, [SimpleNamespace(id=1)])
    with pytest.raises(ValueError, match='explicit'):
        await migration.migrate(args)
    assert state.deleted == []
