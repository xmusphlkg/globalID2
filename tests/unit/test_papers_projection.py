from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.services.data_release.papers_projection import (
    prepare_projection_candidate, project_article, load_projection_candidate, _read_object, PREFIX,
)
from src.services.data_release.papers_content import publish_papers_content
from src.services.data_release.literature_publish import publish_literature_candidate, PREFIX as RESEARCH_PREFIX
from src.services.data_release.surveillance_publish import capture_surveillance_baseline
from tests.unit.test_papers_content import make_template_candidate
from tests.unit.test_surveillance_publish import CONFIG, Store, generation


def article(slug, finding='Public finding'):
    return {'slug': slug, 'article_id': f'lit-{slug}', 'title': f'Paper {slug}', 'editorial_status': 'published',
            'summary': {'en': {'main_findings': finding, 'provenance': {'editorially_approved': False, 'prompt': 'PRIVATE'}},
                        'zh': {'main_findings': '中文发现'}, 'fr': {'main_findings': 'Résultats'}},
            'source_urls': {'doi': 'https://doi.org/10.1/a'}, 'internal_note': 'PRIVATE'}


def initial(tmp_path):
    base = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    return prepare_projection_candidate(base, articles=[article('paper-a'), article('paper-b')],
        directory=tmp_path / 'v3', release_id='candidate', migration=True)[0]


def test_direct_projection_contains_no_private_fields_or_template_versions():
    value = project_article(article('paper-a'))
    assert value['schema'] == 3
    assert 'PRIVATE' not in json.dumps(value)
    assert 'template' not in value and 'head' not in value and 'main' not in value
    with pytest.raises(ValueError, match='already-public'):
        project_article({**article('paper-a'), 'editorial_status': 'draft'})


def test_migration_and_single_body_update_touch_one_record_without_reading_or_rendering_html(tmp_path, monkeypatch):
    first = initial(tmp_path)
    # Delete all old article HTML to prove the new path does not extract or render it.
    for path in first.root.rglob('index.html'):
        path.unlink()
    second, receipt = prepare_projection_candidate(first, articles=[article('paper-a', 'Updated finding')],
        directory=tmp_path / 'next', release_id='second')
    before = {entry['logical_key']: entry for entry in first.entries}
    changed = [entry for entry in second.entries if before[entry['logical_key']] != entry]
    assert len(changed) == 3
    assert all(entry['paper_template']['slug'] == 'paper-a' for entry in changed)
    assert len({entry['key'] for entry in changed}) == 1
    assert receipt['content_record_count'] == 1
    assert receipt['content_render_count'] == receipt['html_assembly_count'] == receipt['astro_build_count'] == 0
    assert all('sha256' not in entry and 'bytes' not in entry for entry in changed)
    assert load_projection_candidate(tmp_path / 'next/candidate.json').generation == second.generation
    store = Store()
    publish_papers_content(first, client=store)
    result = publish_papers_content(second, client=store, previous_objects=first.object_keys)
    assert result['uploaded_objects'] == 1


def test_directory_or_route_changes_fail_closed_on_body_only_path(tmp_path):
    first = initial(tmp_path)
    with pytest.raises(ValueError, match='directory/SEO'):
        prepare_projection_candidate(first, articles=[{**article('paper-a'), 'title': 'New title'}],
            directory=tmp_path / 'bad-title', release_id='second')
    with pytest.raises(ValueError, match='New article routes'):
        prepare_projection_candidate(first, articles=[article('paper-c')], directory=tmp_path / 'new', release_id='new')


def test_template_change_preserves_every_content_record(tmp_path):
    first = initial(tmp_path)
    templates = {}
    for locale in ('en', 'zh', 'fr'):
        entry = next(entry for entry in first.entries if entry.get('paper_template', {}).get('locale') == locale)
        value = _read_object(first, f"{PREFIX}/templates/{entry['paper_template']['template_sha256']}.json")
        value['shell']['chunks'][-1] += '<!-- new compatible shell -->'
        templates[locale] = value
    second, receipt = prepare_projection_candidate(first, articles=[], templates=templates,
        directory=tmp_path / 'new-template', release_id='template')
    assert {entry['key'] for entry in second.entries} == {entry['key'] for entry in first.entries}
    assert receipt['content_record_count'] == receipt['content_render_count'] == 0
    assert receipt['html_assembly_count'] == receipt['template_compile_count'] == 0
    assert first.generation != second.generation


def test_projection_uses_existing_atomic_research_pointer_and_resume(tmp_path):
    papers = initial(tmp_path)
    store = Store(); run = tmp_path / 'run'; run.mkdir()
    config = replace(CONFIG, public_base_url='https://papers.example')
    capture_surveillance_baseline(run, config, prefix=RESEARCH_PREFIX, client=store)
    generation(run, release='candidate')
    args = dict(run=run, objects_dir=run / 'objects', papers=papers, config=config,
                receipt=tmp_path / 'receipt', client=store)
    result = publish_literature_candidate(**args)
    assert result['release_id'] == 'candidate'
    assert store.writes[-1][0] == f'{RESEARCH_PREFIX}/current.json'
    assert not any(key == f'{PREFIX}/current.json' for key, _ in store.writes)
    count = len(store.writes)
    assert publish_literature_candidate(**args)['json']['pointer_changed'] is False
    assert len(store.writes) == count


def test_managed_projection_path_skips_stage_and_recovers_public_verification(tmp_path, monkeypatch):
    from scripts.publish_research_candidate import publish
    from src.generation.literature_publication import freeze_literature_json, select_index_artifacts
    papers = initial(tmp_path)
    config = replace(CONFIG, public_base_url='https://papers.example')
    store = Store()
    source = tmp_path / 'snapshot/public/research'; source.mkdir(parents=True)
    (source / 'index.json').write_text('{"articles":[]}')
    (source / 'catalogue.json').write_text('{}')
    shared = tmp_path / 'shared'
    old_run = tmp_path / 'old-run'; old_run.mkdir()
    capture_surveillance_baseline(old_run, config, prefix=RESEARCH_PREFIX, client=store)
    freeze_literature_json(source, shared, release_id='candidate', generated_at='2026-11-13T02:00:00Z', logical_names=select_index_artifacts(source))
    (old_run / 'current.json').write_bytes((shared / 'current.json').read_bytes())
    publish_literature_candidate(run=old_run, objects_dir=shared / 'objects', papers=papers,
        config=config, receipt=tmp_path / 'receipt', client=store)
    updated, _ = prepare_projection_candidate(papers, articles=[article('paper-a', 'New body')],
        directory=tmp_path / 'body-update', release_id='body-update')
    run = tmp_path / 'new-run'; run.mkdir()
    capture_surveillance_baseline(run, config, prefix=RESEARCH_PREFIX, client=store)
    calls = []
    def verify(candidate):
        calls.append(candidate.generation)
        if len(calls) == 1:
            raise ValueError('Public verification pending')
        return {'status': 'verified'}
    args = dict(run=run, snapshot=tmp_path / 'snapshot', stage=tmp_path / 'NONEXISTENT-NO-ASTRO-STAGE',
                shared=shared, receipt=tmp_path / 'receipt', release_id='body-update', config=config,
                client=store, projection_candidate=tmp_path / 'body-update/candidate.json', projection_verifier=verify)
    with pytest.raises(ValueError, match='feature gate'):
        publish(**args)
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    with pytest.raises(ValueError, match='Public verification pending'):
        publish(**args)
    assert not (run / 'published.json').exists()
    assert json.loads(store.objects[f'{RESEARCH_PREFIX}/current.json'])['papers']['papers_generation_sha256'] == updated.generation
    writes = len(store.writes)
    result = publish(**args)
    assert result['public_verification']['status'] == 'verified'
    assert len(store.writes) == writes
    assert result['json']['pointer_changed'] is False


def test_public_verifier_rejects_wrong_record_even_with_success_status(tmp_path):
    from src.services.data_release.papers_projection import verify_projected_publication
    candidate = initial(tmp_path)
    class Response:
        status = 200
        headers = {'X-GIDS-Paper-Mode': 'projection-v3'}
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return self.url
    with pytest.raises(ValueError, match='version mismatch'):
        verify_projected_publication(candidate, opener=lambda request, **kwargs: Response(request.full_url))


def test_projected_candidate_rejects_legacy_html_receipt_before_any_commit(tmp_path):
    papers = initial(tmp_path)
    store = Store()
    with pytest.raises(ValueError, match='legacy HTML build receipts'):
        publish_literature_candidate(run=tmp_path / 'run', objects_dir=tmp_path / 'objects', papers=papers,
            config=replace(CONFIG, public_base_url='https://papers.example'), receipt=tmp_path / 'receipt',
            client=store, build={'schema': 1})
    assert store.writes == []


def test_public_signal_projection_preserves_grouping_fields_and_excludes_private_signals():
    source = article('paper-a')
    source['related_signals'] = [
        {'signal_id': 'public-1', 'visibility': 'public', 'disease_id': 'covid', 'relation_level': 'historical_disease_geography_context',
         'geographies': [{'code': 'JP', 'name_en': 'Japan', 'name_zh': '日本', 'internal_id': 'PRIVATE'}],
         'window': {'change_pct': 12.5}, 'private_notes': 'PRIVATE'},
        {'signal_id': 'PRIVATE', 'visibility': 'private'},
    ]
    projected = project_article(source)
    signals = projected['content']['related_signals']
    assert len(signals) == 1
    assert signals[0]['relation_level'] == 'historical_disease_geography_context'
    assert signals[0]['geographies'][0]['name_zh'] == '日本'
    assert signals[0]['window']['change_pct'] == 12.5
    assert 'PRIVATE' not in json.dumps(projected)
