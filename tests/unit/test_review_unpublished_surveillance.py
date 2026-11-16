"""Safety boundaries for explicit unpublished-country mapping review."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts import review_unpublished_surveillance as review
from src.services.disease_mapping_config import ReviewedSourceCategoryMapping


def mapping(**changes):
    values = dict(country_code='ET', source_id='SRC_ET_WHO_GHO', source_code='WHS3_62',
        local_name='Measles', target_kind='concept', target_code='D017', mapping_relation='exact',
        comparability='conditional', projection_policy='canonical', aggregation_policy='non_additive',
        definition_version='v1', notes='Reviewed exact source contract', source_path='unused.csv', row_number=2)
    return ReviewedSourceCategoryMapping(**(values | changes))


def test_only_requested_subjects_and_bq_parent_source_are_imported():
    rows = [mapping(), mapping(country_code='NL', source_id=review.BQ_SOURCE),
            mapping(country_code='NL', source_id='SRC_ECDC_ATLAS'),
            mapping(country_code='US', source_id='SRC_US_NNDSS')]
    assert review.select_manifests(rows, {'BQ'}) == [rows[1]]
    assert review.select_manifests(rows, {'ET'}) == [rows[0]]
    with pytest.raises(ValueError, match='Outside unpublished-country scope'):
        review.select_manifests(rows, {'US'})


def test_opaque_codes_and_definition_versions_are_case_sensitive():
    exact = SimpleNamespace(country_code='ET', source_id='SRC_ET_WHO_GHO', source_code='WHS3_62', definition_version='v1')
    other_case = SimpleNamespace(country_code='ET', source_id='SRC_ET_WHO_GHO', source_code='whs3_62', definition_version='v1')
    other_version = SimpleNamespace(country_code='ET', source_id='SRC_ET_WHO_GHO', source_code='WHS3_62', definition_version='v2')
    indexed = {review.category_identity(c): c for c in [exact, other_case, other_version]}
    assert review.match_categories(mapping(), indexed) == [exact]
    assert review.match_categories(mapping(definition_version='*'), indexed) == [exact, other_version]


def test_replacement_preserves_unrelated_active_nl_ecdc_and_other_country_assertions():
    current = [SimpleNamespace(id=1, category_id=10), SimpleNamespace(id=2, category_id=20),
               SimpleNamespace(id=3, category_id=30)]
    reviewed = [SimpleNamespace(id=4, category_id=20)]
    assert review.merged_assertion_ids(current, reviewed, {20}) == {1, 3, 4}


def test_conflict_in_any_of_six_semantic_fields_is_refused_before_writes(tmp_path):
    manifest = tmp_path / 'review.csv'
    manifest.write_text('reviewed contract', encoding='utf-8')
    expected = mapping(source_path=str(manifest))
    for field, value in [('target_kind', 'group'), ('target_code', 'D002'),
                          ('mapping_relation', 'narrower'), ('comparability', 'direct'),
                          ('projection_policy', 'no_projection'), ('aggregation_policy', 'direct_only')]:
        existing = SimpleNamespace(id=5, assertion_status='approved',
            **dict(zip(review.SEMANTIC_FIELDS, review.semantics(replace(expected, **{field: value})))))
        with pytest.raises(ValueError, match='Conflicting reviewed mapping'):
            asyncio.run(review.apply_review(None, [(SimpleNamespace(id=1), expected)], {1: [existing]}, supersede=False))


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: 123)

    async def rollback(self):
        self.rollbacks += 1

    async def commit(self):
        self.commits += 1


def install_run_stubs(monkeypatch, tmp_path, *, conflicts=None):
    db = FakeDb()
    manifest = tmp_path / 'et.csv'
    manifest.write_text('reviewed contract', encoding='utf-8')
    monkeypatch.setattr(review, 'DEFAULT_MAPPING_DIR', tmp_path)

    @asynccontextmanager
    async def database():
        yield db

    async def preflight(_):
        pass

    async def prepare(_, mappings):
        return [(SimpleNamespace(id=1), mappings[0])], {}, [], conflicts or []

    monkeypatch.setattr(review, 'get_database', database)
    monkeypatch.setattr(review, 'preflight', preflight)
    monkeypatch.setattr(review, 'load_reviewed_source_category_mappings', lambda: [mapping(source_path=str(manifest))])
    monkeypatch.setattr(review, 'prepare_review', prepare)
    return db


def test_default_audit_enforces_read_only_and_never_creates_release(monkeypatch, tmp_path):
    db = install_run_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(review, 'apply_review', lambda *args, **kwargs: pytest.fail('audit attempted mapping writes'))
    result = asyncio.run(review.run(countries={'ET'}))
    assert db.statements[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert not any('advisory' in statement for statement in db.statements)
    assert db.commits == 0 and db.rollbacks == 1
    assert result['active_release_before'] == 123
    assert result['transaction'] == 'rolled_back'


def test_rehearsal_activates_inside_transaction_then_rolls_back(monkeypatch, tmp_path):
    db = install_run_stubs(monkeypatch, tmp_path)
    events = []

    async def apply(_, plans, assertions, **kwargs):
        events.append('mappings_applied')
        return [SimpleNamespace(id=9)], []

    async def release(_, chosen, ids):
        events.append('release_activated')
        return SimpleNamespace(id=999, release_code='REHEARSAL', status='active', checksum='sha', metadata_={})

    monkeypatch.setattr(review, 'apply_review', apply)
    monkeypatch.setattr(review, 'create_scoped_release', release)
    result = asyncio.run(review.run(mode='rehearse', countries={'ET'}))
    assert events == ['mappings_applied', 'release_activated']
    assert db.commits == 0 and db.rollbacks == 1
    assert result['transaction'] == 'rolled_back'
    assert result['release']['status'] == 'active'
    assert any('pg_advisory_xact_lock' in statement for statement in db.statements)


def test_conflicts_prevent_apply_and_commit_without_explicit_override(monkeypatch, tmp_path):
    db = install_run_stubs(monkeypatch, tmp_path, conflicts=[{'category_id': 1}])
    monkeypatch.setattr(review, 'apply_review', lambda *args, **kwargs: pytest.fail('conflict was ignored'))
    result = asyncio.run(review.run(mode='apply', countries={'ET'}))
    assert result['ready'] is False
    assert db.commits == 0 and db.rollbacks == 1
    assert 'release' not in result


def test_unexpected_failure_rolls_back_without_committing(monkeypatch, tmp_path):
    db = install_run_stubs(monkeypatch, tmp_path)

    async def failure(*args, **kwargs):
        raise RuntimeError('mapping validation failed')

    monkeypatch.setattr(review, 'apply_review', failure)
    with pytest.raises(RuntimeError, match='mapping validation failed'):
        asyncio.run(review.run(mode='apply', countries={'ET'}))
    assert db.commits == 0 and db.rollbacks == 1


def test_explicit_supersession_preserves_old_decision_and_exact_manifest_evidence(tmp_path):
    manifest = tmp_path / 'review.csv'
    manifest.write_text('reviewed local contract\n', encoding='utf-8')
    row = mapping(source_path=str(manifest))
    old = SimpleNamespace(id=5, assertion_key='AI-MAP', assertion_status='approved',
        reviewed_by='ai-peer-review', review_notes='Original review', metadata_={'original': True},
        **dict(zip(review.SEMANTIC_FIELDS, review.semantics(replace(row, aggregation_policy='direct_only')))))
    category = SimpleNamespace(id=1, definition_version='v1', status='discovered', ai_status='completed', ai_next_attempt_at=None)

    class Db:
        def __init__(self):
            self.added = []

        def add(self, value):
            self.added.append(value)

        async def execute(self, *args, **kwargs):
            pass

        async def flush(self):
            for item in self.added:
                item.id = 99

    db = Db()
    chosen, changes = asyncio.run(review.apply_review(db, [(category, row)], {1: [old]}, supersede=True))
    assert old.assertion_status == 'superseded'
    assert old.aggregation_policy == 'direct_only'
    assert old.reviewed_by == 'ai-peer-review' and old.review_notes == 'Original review'
    assert old.metadata_['original'] is True
    assert chosen[0].aggregation_policy == 'non_additive'
    assert chosen[0].supersedes_assertion_id == old.id
    assert chosen[0].metadata_['superseded_assertions'][0]['semantics']['aggregation_policy'] == 'direct_only'
    evidence = chosen[0].evidence[0]
    assert evidence['source_code'] == 'WHS3_62'
    assert evidence['actual_definition_version'] == 'v1'
    assert len(evidence['manifest_sha256']) == 64
    assert changes[0]['prior'][0]['id'] == old.id


@pytest.mark.parametrize('mode', ['apply', 'rehearse'])
def test_manifest_drift_after_release_creation_rolls_back(mode, monkeypatch, tmp_path):
    db = install_run_stubs(monkeypatch, tmp_path)

    async def apply(_, plans, assertions, **kwargs):
        assert str(tmp_path / 'et.csv') in kwargs['frozen_manifests']
        return [SimpleNamespace(id=9)], []

    async def release(_, chosen, ids):
        (tmp_path / 'et.csv').write_text('changed semantics after loading', encoding='utf-8')
        return SimpleNamespace(id=999, release_code='DRIFT', status='active', checksum='sha', metadata_={})

    monkeypatch.setattr(review, 'apply_review', apply)
    monkeypatch.setattr(review, 'create_scoped_release', release)
    with pytest.raises(RuntimeError, match='manifest changed during review'):
        asyncio.run(review.run(mode=mode, countries={'ET'}))
    assert db.commits == 0
    assert db.rollbacks == 1


def test_manifest_changed_while_loader_reads_is_rejected(monkeypatch, tmp_path):
    manifest = tmp_path / 'et.csv'
    manifest.write_text('first revision', encoding='utf-8')
    monkeypatch.setattr(review, 'DEFAULT_MAPPING_DIR', tmp_path)

    def load():
        manifest.write_text('new revision', encoding='utf-8')
        return [mapping(source_path=str(manifest))]

    monkeypatch.setattr(review, 'load_reviewed_source_category_mappings', load)
    with pytest.raises(RuntimeError, match='manifest changed during review'):
        review.load_frozen_manifests({'ET'})
