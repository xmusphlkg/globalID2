"""Full-site research uses the literature publisher's single frozen transaction."""
from dataclasses import replace
import hashlib
import json

import pytest

from src.services.data_release.literature_publish import PREFIX
from src.services.data_release.papers_r2 import R2S3Error
from src.services.data_release.site_research_publish import freeze_site_research, publish_site_research
from src.services.data_release.surveillance_publish import capture_surveillance_baseline
from tests.unit.test_prepare_papers_templates import page
from tests.unit.test_surveillance_publish import CONFIG, Store


def sources(tmp_path):
    papers, dist, research, articles = (tmp_path / name for name in ('papers', 'dist', 'research-json', 'articles'))
    for prefix in ('', 'zh', 'fr'):
        article = papers / prefix / 'research/articles/paper-a/index.html'
        article.parent.mkdir(parents=True)
        article.write_text(page('paper-a', 'style-one'))
        landing = dist / prefix / 'research/index.html'
        landing.parent.mkdir(parents=True)
        landing.write_text(f'<html>landing {prefix or "en"}</html>')
    (papers / '_astro').mkdir()
    (papers / '_astro/old.css').write_text('body{color:red}')
    (dist / '_astro').mkdir()
    (dist / '_astro/new.css').write_text('body{color:blue}')
    (dist / 'scripts').mkdir()
    (dist / 'scripts/research.js').write_text('window.research=true;')
    (dist / 'research/catalogue').mkdir()
    (dist / 'research/catalogue/manifest.json').write_text('{"catalogue":"runtime"}')
    (research / 'shards').mkdir(parents=True)
    (research / 'index.json').write_text('{"file":"shards/list.json","version":1}')
    (research / 'catalogue.json').write_text('{"articles":1}')
    (research / 'shards/list.json').write_text('{"articles":["paper-a"]}')
    articles.mkdir()
    (articles / 'paper-a.json').write_text(json.dumps({
        'slug': 'paper-a', 'article_id': 'lit-paper-a', 'title': 'Paper paper-a',
        'editorial_status': 'published',
        'summary': {'en': {'main_findings': 'Public finding'}, 'zh': {'main_findings': '中文发现'},
                    'fr': {'main_findings': 'Résultats'}},
    }))
    config = replace(CONFIG, public_base_url='https://papers.example')
    args = dict(papers_dir=papers, site_dist=dist, research_source=research, articles_dir=articles,
                run=tmp_path / 'run', release_id='site-one', config=config, receipt=tmp_path / 'receipt.json',
                projection_verifier=lambda candidate: {'status': 'verified', 'generation': candidate.generation})
    return args


def entries(store, pointer):
    catalogue = json.loads(store.get_object(pointer['papers']['papers_manifest_url'].removeprefix('https://papers.example/')))
    return {entry['logical_key']: entry for shard in catalogue['shards'].values()
            for entry in json.loads(store.get_object(shard['key']))['items']}


def test_site_requires_prebuild_baseline_without_mutating_remote(tmp_path):
    args, store = sources(tmp_path), Store()
    with pytest.raises(ValueError, match='baseline before building'):
        publish_site_research(**args, client=store)
    assert not store.writes
    assert not args['run'].exists()


def test_site_merges_all_locales_assets_index_and_template_articles_before_one_switch(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    result = publish_site_research(**args, client=store, workers=2)
    pointer = json.loads(store.get_object(f'{PREFIX}/current.json'))
    published = entries(store, pointer)
    assert result['release_id'] == pointer['release_id'] == 'site-one'
    assert result['public_verification'] == {'status': 'verified', 'generation': pointer['papers']['papers_generation_sha256']}
    assert store.writes[-1][0] == f'{PREFIX}/current.json'
    for key in ('research/', 'zh/research/', 'fr/research/', '_astro/old.css', '_astro/new.css',
                'scripts/research.js', 'research/catalogue/manifest.json'):
        assert key in published
    assert not any(key.startswith('site-data/research/') for key in published)
    article_entries = [entry for entry in published.values() if 'paper_template' in entry]
    assert len(article_entries) == 3
    assert {entry['paper_template']['locale'] for entry in article_entries} == {'en', 'zh', 'fr'}
    assert len({entry['key'] for entry in article_entries}) == 1
    for entry in article_entries:
        assert entry['integrity'] == 'independent-v3'
        assert entry['paper_template']['schema'] == 3
        assert 'sha256' not in entry and 'bytes' not in entry
        assert entry['key'].startswith('paper-template-v3/records/')
    assert not any(key.endswith('/research/articles/paper-a/') for key in store.objects)
    assert 'paper-template-v2/current.json' not in store.objects
    assert set(pointer['files']) == {'index.json', 'catalogue.json', 'shards/list.json'}
    writes = list(store.writes)
    resumed = publish_site_research(**args, client=store)
    assert resumed['json']['pointer_changed'] is False
    assert store.writes == writes


def test_failed_switch_retries_frozen_site_instead_of_new_mutable_source(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    store.failure = '/current.json'
    with pytest.raises(R2S3Error):
        publish_site_research(**args, client=store, workers=2)
    assert f'{PREFIX}/current.json' not in store.objects
    assert not args['receipt'].exists()
    frozen = json.loads((args['run'] / 'projection-v3/candidate.json').read_bytes())
    frozen_entry = next(entry for entry in frozen['entries'] if entry['logical_key'] == 'research/articles/paper-a/')
    article = args['papers_dir'] / 'research/articles/paper-a/index.html'
    article.write_text(page('paper-a', 'newer-source'))
    (args['research_source'] / 'index.json').write_text('{"version":2}')
    store.failure = None
    publish_site_research(**args, client=store, workers=2)
    pointer = json.loads(store.get_object(f'{PREFIX}/current.json'))
    descriptor = entries(store, pointer)['research/articles/paper-a/']
    assert descriptor == frozen_entry
    assert descriptor['integrity'] == 'independent-v3'
    index = pointer['files']['index.json']
    assert json.loads(store.get_object(f"{PREFIX}/{index['url']}"))['version'] == 1
    assert store.writes[-1][0] == f'{PREFIX}/current.json'


def test_site_canary_failure_keeps_committed_candidate_for_verification_only_retry(tmp_path, monkeypatch):
    from src.services.data_release import site_research_publish as module
    args, store = sources(tmp_path), Store()
    args.pop('projection_verifier')
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    attempts = []

    def verify(candidate):
        pointer = json.loads(store.get_object(f'{PREFIX}/current.json'))
        assert pointer['papers']['papers_generation_sha256'] == candidate.generation
        authority = json.loads((args['run'] / 'authority-committed.json').read_bytes())
        assert authority['authoritative_committed'] is True
        assert authority['public_verification_required'] is True
        attempts.append(candidate.generation)
        if len(attempts) == 1:
            raise ValueError('Projected public canary version mismatch')
        return {'status': 'verified', 'generation': candidate.generation}

    monkeypatch.setattr(module, 'verify_projected_publication', verify)
    with pytest.raises(ValueError, match='canary version mismatch'):
        publish_site_research(**args, client=store)
    assert not (args['run'] / 'published.json').exists()
    committed = store.get_object(f'{PREFIX}/current.json')
    writes = list(store.writes)
    article = args['articles_dir'] / 'paper-a.json'
    article.write_text('{"unrelated":"later mutable source"}')
    result = publish_site_research(**args, client=store)
    assert attempts == [result['papers']['generation_sha256']] * 2
    assert result['json']['pointer_changed'] is False
    assert result['public_verification']['status'] == 'verified'
    assert store.get_object(f'{PREFIX}/current.json') == committed
    assert store.writes == writes
    assert json.loads((args['run'] / 'published.json').read_bytes()) == result


def test_site_preparation_does_not_probe_or_publish_public_candidate(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)

    def forbidden_probe(candidate):
        pytest.fail('Preparation must not probe an unpublished candidate')

    args['projection_verifier'] = forbidden_probe
    result = publish_site_research(**args, client=store, prepare_only=True)
    assert result['generation']
    assert store.writes == []
    assert not (args['run'] / 'published.json').exists()


def test_changed_article_updates_shared_record_and_json_in_same_generation(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store, workers=2)
    first = json.loads(store.get_object(f'{PREFIX}/current.json'))
    first_entries = entries(store, first)
    old_keys = set(store.objects)
    store.writes.clear()
    article = json.loads((args['articles_dir'] / 'paper-a.json').read_text())
    article['summary']['en']['main_findings'] = 'New public finding'
    (args['articles_dir'] / 'paper-a.json').write_text(json.dumps(article))
    (args['research_source'] / 'index.json').write_text('{"file":"shards/list.json","version":2}')
    second_args = {**args, 'run': tmp_path / 'second-run', 'release_id': 'site-two'}
    capture_surveillance_baseline(second_args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**second_args, client=store, workers=2)
    second = json.loads(store.get_object(f'{PREFIX}/current.json'))
    second_entries = entries(store, second)
    assert first['papers']['papers_generation_sha256'] != second['papers']['papers_generation_sha256']
    assert first_entries['research/articles/paper-a/']['key'] != second_entries['research/articles/paper-a/']['key']
    assert sum(key.startswith('paper-template-v3/records/') for key, _ in store.writes) == 1
    assert not any(key.startswith('paper-template-v2/') for key, _ in store.writes)
    assert old_keys <= set(store.objects)
    assert json.loads(store.get_object(f"{PREFIX}/{second['files']['index.json']['url']}"))['version'] == 2
    assert store.writes[-1][0] == f'{PREFIX}/current.json'


def test_intervening_literature_publication_cannot_be_overwritten_by_site(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    newer = b'{"schema_version":1,"release_id":"newer-literature","files":{}}'
    store.objects[f'{PREFIX}/current.json'] = newer
    with pytest.raises(R2S3Error, match='baseline changed'):
        publish_site_research(**args, client=store, workers=2)
    assert store.objects[f'{PREFIX}/current.json'] == newer
    assert not store.writes


def test_json_pointer_projection_comes_from_frozen_copy_not_later_mutable_source(tmp_path, monkeypatch):
    from src.services.data_release import site_research_publish as module
    args = sources(tmp_path)
    original_index = (args['research_source'] / 'index.json').read_bytes()
    original = module.freeze_literature_json
    def change_source_then_freeze(source, destination, **kwargs):
        (args['research_source'] / 'index.json').write_text('{"version":999}')
        return original(source, destination, **kwargs)
    monkeypatch.setattr(module, 'freeze_literature_json', change_source_then_freeze)
    freeze_site_research(papers_dir=args['papers_dir'], site_dist=args['site_dist'], research_source=args['research_source'],
                        run=args['run'], release_id=args['release_id'], papers_origin=args['config'].public_base_url)
    pointer = json.loads((args['run'] / 'current.json').read_bytes())
    public_index = (args['run'] / 'json' / pointer['files']['index.json']['url']).read_bytes()
    assert original_index == public_index
    assert not (args['run'] / 'bundle/site-data/research/index.json').exists()


def test_unsafe_source_and_other_release_ready_receipt_cannot_be_adopted(tmp_path):
    args = sources(tmp_path)
    link = args['site_dist'] / 'scripts/unsafe.js'
    link.symlink_to(tmp_path / 'outside.js')
    with pytest.raises(ValueError, match='Symlink'):
        freeze_site_research(papers_dir=args['papers_dir'], site_dist=args['site_dist'], research_source=args['research_source'],
                            run=args['run'], release_id=args['release_id'], papers_origin=args['config'].public_base_url)
    assert not (args['run'] / 'site-ready.json').exists()
    link.unlink()
    freeze_site_research(papers_dir=args['papers_dir'], site_dist=args['site_dist'], research_source=args['research_source'],
                        run=args['run'], release_id=args['release_id'], papers_origin=args['config'].public_base_url)
    with pytest.raises(ValueError, match='another release'):
        freeze_site_research(papers_dir=args['papers_dir'], site_dist=args['site_dist'], research_source=args['research_source'],
                            run=args['run'], release_id='other-release', papers_origin=args['config'].public_base_url)


def test_style_release_refuses_stale_research_projection_without_remote_writes(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store, workers=2)
    previous = store.get_object(f'{PREFIX}/current.json')
    store.writes.clear()
    (args['research_source'] / 'index.json').write_text('{"version":"stale local export"}')
    next_args = {**args, 'run': tmp_path / 'style-run', 'release_id': 'style-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    with pytest.raises(ValueError, match='differs from the captured published snapshot'):
        publish_site_research(**next_args, client=store, preserve_research_data=True, workers=2)
    assert store.get_object(f'{PREFIX}/current.json') == previous
    assert not store.writes


def test_style_release_preserves_published_json_and_reuses_all_article_records(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store, workers=2)
    first = json.loads(store.get_object(f'{PREFIX}/current.json'))
    store.writes.clear()
    for prefix in ('', 'zh', 'fr'):
        (args['papers_dir'] / prefix / 'research/articles/paper-a/index.html').write_text(page('paper-a', 'style-two'))
    next_args = {**args, 'run': tmp_path / 'style-run', 'release_id': 'style-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**next_args, client=store, preserve_research_data=True, workers=2)
    second = json.loads(store.get_object(f'{PREFIX}/current.json'))
    assert first['files'] == second['files']
    assert first['papers']['papers_generation_sha256'] != second['papers']['papers_generation_sha256']
    assert any(key.startswith('paper-template-v3/templates/') for key, _ in store.writes)
    assert not any(key.startswith('paper-template-v3/records/') for key, _ in store.writes)
    assert not any(key.startswith('paper-template-v2/') for key, _ in store.writes)
    assert store.writes[-1][0] == f'{PREFIX}/current.json'


def independent_site_sources(args):
    for path in args['papers_dir'].glob('**/articles/*/index.html'):
        path.unlink()
    (args['papers_dir'] / '_astro/old.css').unlink()
    (args['site_dist'] / '_astro/template-update.css').write_text('body{color:green}')
    (args['site_dist'] / 'research/index.html').write_text('<html>Updated research directory</html>')


def test_independent_site_preserves_complete_v3_articles_and_freezes_before_upload(tmp_path):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store)
    first = json.loads(store.get_object(f'{PREFIX}/current.json'))
    original_entries = entries(store, first)
    independent_site_sources(args)
    next_args = {**args, 'run': tmp_path / 'independent', 'release_id': 'site-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    writes = list(store.writes)
    prepared = publish_site_research(**next_args, client=store, prepare_only=True)
    assert prepared['article_pages'] == 3
    assert store.writes == writes
    assert store.get_object(f'{PREFIX}/current.json') == json.dumps(first, sort_keys=True, separators=(',', ':')).encode()
    frozen = json.loads((next_args['run'] / 'projection-v3/candidate.json').read_bytes())
    projected = {item['logical_key']: item for item in frozen['entries']}
    for key, value in original_entries.items():
        if 'paper_template' in value:
            assert projected[key] == value
    assert projected['_astro/old.css'] == original_entries['_astro/old.css']
    assert '_astro/template-update.css' in projected
    assert projected['research/'] != original_entries['research/']
    # Prepared publication must no longer depend on mutable build outputs or
    # on the previous release's remote immutable objects.
    for path in args['papers_dir'].glob('**/*'):
        if path.is_file():
            path.unlink()
    result = publish_site_research(**next_args, client=store)
    second = json.loads(store.get_object(f'{PREFIX}/current.json'))
    assert second['files'] == first['files']
    assert result['public_verification']['status'] == 'verified'
    assert entries(store, second) == projected


@pytest.mark.parametrize('local_baseline', [False, True])
def test_independent_site_removes_retired_non_article_pages_without_losing_papers(tmp_path, local_baseline):
    args, store = sources(tmp_path), Store()
    if local_baseline:
        args['run'] = tmp_path / 'exports/site-research-runs/first-task/site-one'
    retired_paths = []
    for locale in ('', 'zh', 'fr'):
        path = args['site_dist'] / locale / 'research/collections/retired/index.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<html>Retired collection</html>')
        retired_paths.append(path)
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store)
    before = entries(store, json.loads(store.get_object(f'{PREFIX}/current.json')))
    retired_keys = {f'{locale + "/" if locale else ""}research/collections/retired/' for locale in ('', 'zh', 'fr')}
    assert retired_keys.issubset(before)
    for path in retired_paths:
        path.unlink()
    independent_site_sources(args)
    next_args = {**args, 'run': tmp_path / 'exports/site-research-runs/second-task/site-two', 'release_id': 'site-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**next_args, client=store)
    after = entries(store, json.loads(store.get_object(f'{PREFIX}/current.json')))
    assert not retired_keys.intersection(after)
    for key, entry in before.items():
        if 'paper_template' in entry:
            assert after[key] == entry
    assert after['_astro/old.css'] == before['_astro/old.css']
    assert after['scripts/research.js'] == before['scripts/research.js']
    assert after['research/'] != before['research/']
    # Removing a route does not delete immutable history or supporting bytes.
    assert all(before[key]['key'] in store.objects for key in retired_keys)
    frozen = next_args['run'] / 'bundle'
    assert not any(frozen.glob('**/collections/retired/index.html'))


@pytest.mark.parametrize('fault', ['missing_baseline', 'v2_baseline', 'missing_record', 'changed_json'])
def test_independent_site_rejects_unproven_or_incompatible_baseline_without_upload(tmp_path, fault):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store)
    first = json.loads(store.get_object(f'{PREFIX}/current.json'))
    original_entries = entries(store, first)
    independent_site_sources(args)
    if fault == 'missing_baseline':
        store.objects.pop(f'{PREFIX}/current.json')
    elif fault == 'missing_record':
        article = next(entry for entry in original_entries.values() if 'paper_template' in entry)
        store.objects.pop(article['key'])
    elif fault == 'changed_json':
        (args['research_source'] / 'index.json').write_text('{"unpublished":true}')
    else:
        from src.services.data_release.papers_content import catalogue_from_entries
        changed = list(original_entries.values())
        for entry in changed:
            if 'paper_template' in entry:
                entry['paper_template']['schema'] = 2
        old = catalogue_from_entries(root=args['papers_dir'], origin=args['config'].public_base_url,
            release_id=first['release_id'], entries=changed)
        for key, body in old.shards:
            store.objects[key] = body
        store.objects[old.manifest_key] = old.manifest
        first['papers'].update(papers_manifest_url=old.origin + '/' + old.manifest_key,
            papers_manifest_sha256=hashlib.sha256(old.manifest).hexdigest(), papers_generation_sha256=old.generation)
        store.objects[f'{PREFIX}/current.json'] = json.dumps(first).encode()
    next_args = {**args, 'run': tmp_path / 'independent', 'release_id': 'site-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    writes = list(store.writes)
    pointer = store.get_object(f'{PREFIX}/current.json')
    with pytest.raises(ValueError):
        publish_site_research(**next_args, client=store, prepare_only=True)
    assert store.writes == writes
    assert store.get_object(f'{PREFIX}/current.json') == pointer
    assert not (next_args['run'] / 'papers-prepared.json').exists()


def test_independent_site_prepared_retry_refuses_newer_research_pointer(tmp_path, monkeypatch):
    args, store = sources(tmp_path), Store()
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store)
    independent_site_sources(args)
    next_args = {**args, 'run': tmp_path / 'independent', 'release_id': 'site-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**next_args, client=store, prepare_only=True)
    monkeypatch.setenv('GIDS_PUBLICATION_RECOVERY_ONLY', '1')
    newer = b'{"release_id":"newer-literature"}'
    store.objects[f'{PREFIX}/current.json'] = newer
    writes = list(store.writes)
    with pytest.raises(R2S3Error, match='baseline changed'):
        publish_site_research(**next_args, client=store)
    assert store.writes == writes
    assert store.get_object(f'{PREFIX}/current.json') == newer


@pytest.mark.parametrize('tamper', [False, True])
def test_independent_site_verifies_local_committed_candidate_before_reuse(tmp_path, tamper):
    args, store = sources(tmp_path), Store()
    args['run'] = tmp_path / 'exports/site-research-runs/first-task/site-one'
    capture_surveillance_baseline(args['run'], args['config'], prefix=PREFIX, client=store)
    publish_site_research(**args, client=store)
    independent_site_sources(args)
    next_args = {**args, 'run': tmp_path / 'exports/site-research-runs/second-task/site-two', 'release_id': 'site-two'}
    capture_surveillance_baseline(next_args['run'], args['config'], prefix=PREFIX, client=store)
    candidate_path = args['run'] / 'projection-v3/candidate.json'
    if tamper:
        frozen = json.loads(candidate_path.read_bytes())
        frozen['template_objects'] = []
        candidate_path.write_text(json.dumps(frozen))
    def no_remote_read(key):
        pytest.fail('A verified local baseline must not download remote objects')
    store.get_object = no_remote_read
    writes = list(store.writes)
    if tamper:
        with pytest.raises(ValueError, match='missing dependencies'):
            publish_site_research(**next_args, client=store, prepare_only=True)
    else:
        assert publish_site_research(**next_args, client=store, prepare_only=True)['article_pages'] == 3
    assert store.writes == writes
