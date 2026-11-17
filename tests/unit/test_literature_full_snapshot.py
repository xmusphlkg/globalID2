"""Complete JSON snapshots can add/remove routes without article HTML."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.publish_research_candidate import publish
from src.generation.literature_publication import freeze_literature_json, select_index_artifacts
from src.services.data_release import literature_projection
from src.services.data_release.literature_artifacts import build_research_papers_manifest
from src.services.data_release.literature_publish import PREFIX as RESEARCH_PREFIX, publish_literature_candidate
from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_content import prepare_papers_content_from_manifest, publish_papers_content
from src.services.data_release.papers_immutable import ImmutablePapersError
from src.services.data_release.papers_projection import (
    PREFIX, _read_object, assert_v3_candidate, load_projection_candidate, prepare_full_snapshot_candidate,
    prepare_projection_candidate,
    verify_projected_publication,
)
from src.services.data_release.surveillance_publish import capture_surveillance_baseline
from tests.unit.test_papers_projection import article, initial
from tests.unit.test_surveillance_publish import CONFIG, Store


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def snapshot_and_collections(tmp_path, articles, *, release_id='next', collection='new'):
    snapshot = tmp_path / 'snapshot'
    digests = {item['slug']: write(snapshot / f'build/research/articles/{item["slug"]}.json', item) for item in articles}
    build = {'schema': 1, 'scope': 'collections', 'article_pages_rendered': 0,
        'article_pages_restored': 0, 'snapshot': str(snapshot), 'template_fingerprint': 'a' * 64}
    build['article_inputs_sha256'] = write(snapshot / 'article-inputs.json', {'schema': 1, 'articles': digests})
    build['routes_sha256'] = write(snapshot / 'build/research/routes.json', {
        'articles': [{'slug': item['slug']} for item in articles]})
    build['research_index_sha256'] = write(snapshot / 'build/research/index.json', {'articles': articles, 'preprints': []})
    build['public_research_index_sha256'] = write(snapshot / 'public/research/index.json', {'articles': articles})
    build['public_research_catalogue_sha256'] = write(snapshot / 'public/research/catalogue.json', {'articles': articles})
    stage = tmp_path / 'stage'
    pages = {}
    for prefix in ('', 'zh/', 'fr/'):
        for relative in ('research/index.html', f'research/topics/{collection}/index.html'):
            path = stage / (prefix + relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'<html>{prefix}{collection}:{[item["title"] for item in articles]}</html>')
            pages[prefix + relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    build['pages'] = pages
    Path(f'{stage}.inputs.json').write_text(json.dumps(build))
    manifest = build_research_papers_manifest(stage, origin='https://papers.example', build=build)
    static = prepare_papers_content_from_manifest(stage, manifest, papers_origin='https://papers.example', release_id=release_id)
    return snapshot, stage, build, static


def test_full_snapshot_adds_deletes_and_rebuilds_all_locales_without_article_html(tmp_path):
    base = initial(tmp_path / 'base')
    for path in base.root.rglob('index.html'):
        path.unlink()
    updated = {**article('paper-a'), 'title': 'New title', 'diseases': [{'disease_id': 'new', 'name_en': 'New'}],
               'countries': [{'code': 'JP'}], 'topics': [{'id': 'new'}]}
    snapshot, stage, build, static = snapshot_and_collections(tmp_path / 'new', [updated, article('paper-c')])
    articles = literature_projection.complete_snapshot_articles(snapshot, build)
    candidate, receipt = prepare_full_snapshot_candidate(base, articles=articles, static=static,
        directory=tmp_path / 'candidate', release_id='next')
    projected = [entry for entry in candidate.entries if 'paper_template' in entry]
    assert len(projected) == 6
    assert {(entry['paper_template']['slug'], entry['paper_template']['locale']) for entry in projected} == {
        (slug, locale) for slug in ('paper-a', 'paper-c') for locale in ('en', 'zh', 'fr')}
    record = _read_object(candidate, next(entry['key'] for entry in projected if entry['paper_template']['slug'] == 'paper-a'))
    assert record['content']['title'] == 'New title'
    assert record['content']['diseases'][0]['disease_id'] == 'new'
    assert receipt['deleted_slugs'] == ['paper-b'] and receipt['added_slugs'] == ['paper-c']
    assert receipt['changed_slugs'] == ['paper-a', 'paper-c']
    assert receipt['article_pages_rendered'] == receipt['article_pages_restored'] == receipt['html_assembly_count'] == 0
    assert not (stage / 'research/articles').exists()
    assert {entry['path'] for entry in candidate.entries if 'paper_template' not in entry and entry['path'].endswith('.html')} == set(build['pages'])


def test_last_article_deletion_and_first_article_after_empty_preserve_templates(tmp_path):
    base = initial(tmp_path / 'base')
    _, _, _, static = snapshot_and_collections(tmp_path / 'empty', [])
    empty, receipt = prepare_full_snapshot_candidate(base, articles=[], static=static,
        directory=tmp_path / 'empty-v3', release_id='next')
    assert_v3_candidate(empty)
    assert receipt['deleted_slugs'] == ['paper-a', 'paper-b']
    assert not any('paper_template' in entry for entry in empty.entries)
    _, _, _, first_static = snapshot_and_collections(tmp_path / 'first', [article('paper-z')], release_id='first')
    first, receipt = prepare_full_snapshot_candidate(empty, articles=[article('paper-z')], static=first_static,
        directory=tmp_path / 'first-v3', release_id='first')
    assert len([entry for entry in first.entries if 'paper_template' in entry]) == 3
    assert receipt['added_slugs'] == ['paper-z']


def test_unchanged_full_snapshot_keeps_record_hashes_and_replaces_collection_routes(tmp_path):
    base = initial(tmp_path / 'base')
    _, _, _, static = snapshot_and_collections(tmp_path / 'same', [article('paper-a'), article('paper-b')])
    candidate, receipt = prepare_full_snapshot_candidate(base, articles=[article('paper-a'), article('paper-b')],
        static=static, directory=tmp_path / 'v3', release_id='next')
    before = {entry['key'] for entry in base.entries if 'paper_template' in entry}
    assert {entry['key'] for entry in candidate.entries if 'paper_template' in entry} == before
    assert receipt['unchanged_slugs'] == ['paper-a', 'paper-b']
    assert receipt['changed_slugs'] == receipt['deleted_slugs'] == []


@pytest.mark.parametrize('damage', ['article', 'routes', 'index', 'receipt', 'collection', 'public_index', 'catalogue'])
def test_snapshot_or_collection_changes_after_render_are_rejected(tmp_path, damage):
    snapshot, stage, build, _ = snapshot_and_collections(tmp_path, [article('paper-a')])
    paths = {'article': snapshot / 'build/research/articles/paper-a.json',
        'routes': snapshot / 'build/research/routes.json', 'index': snapshot / 'build/research/index.json',
        'receipt': snapshot / 'article-inputs.json', 'collection': stage / 'research/index.html',
        'public_index': snapshot / 'public/research/index.json', 'catalogue': snapshot / 'public/research/catalogue.json'}
    paths[damage].write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        literature_projection.verify_snapshot_collections(stage, build)
        literature_projection.complete_snapshot_articles(snapshot, build)


def test_duplicate_slug_and_missing_template_asset_fail_before_publication(tmp_path):
    base = initial(tmp_path / 'base')
    _, _, _, static = snapshot_and_collections(tmp_path / 'static', [])
    with pytest.raises(ValueError, match='Duplicate'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-a'), article('paper-a')],
            static=static, directory=tmp_path / 'duplicate', release_id='next')
    item = next(item for item in base.template_objects if item['key'].startswith(f'{PREFIX}/templates/'))
    (base.template_root / item['path']).unlink()
    with pytest.raises((ValueError, ImmutablePapersError)):
        prepare_full_snapshot_candidate(base, articles=[article('paper-c')],
            static=static, directory=tmp_path / 'missing', release_id='next')


def test_template_baseline_freezes_dependencies_and_missing_cache_requests_full_build(tmp_path, monkeypatch):
    base = initial(tmp_path / 'base')
    literature_projection.save_template_compatibility(base, {'template_fingerprint': 'a' * 64})
    monkeypatch.setattr(literature_projection, '_local_candidate', lambda *args: base)
    previous = {'papers': {'papers_generation_sha256': base.generation}}
    run = tmp_path / 'run'; run.mkdir()
    marker = literature_projection.freeze_projection_build_baseline(run=run, previous=previous, origin=base.origin)
    assert marker['generation'] == base.generation
    assert Path(marker['candidate']).is_file()
    (base.template_root / base.template_objects[0]['path']).unlink()
    # The first candidate has its own copy; a future build must fall back.
    assert Path(marker['candidate']).is_file()
    next_run = tmp_path / 'missing'; next_run.mkdir()
    assert literature_projection.freeze_projection_build_baseline(run=next_run, previous=previous, origin=base.origin)['mode'] == 'full'


@pytest.mark.parametrize('mismatched_index', [False, True])
def test_full_snapshot_managed_publish_updates_index_and_recovers_failed_public_canary(tmp_path, monkeypatch, mismatched_index):
    base = initial(tmp_path / 'base')
    config = replace(CONFIG, public_base_url=base.origin)
    store = Store()
    old_snapshot, _, _, _ = snapshot_and_collections(tmp_path / 'old', [article('paper-a'), article('paper-b')])
    old_run = tmp_path / 'old-run'; old_run.mkdir()
    shared = tmp_path / 'shared'
    capture_surveillance_baseline(old_run, config, prefix=RESEARCH_PREFIX, client=store)
    source = old_snapshot / 'public/research'
    freeze_literature_json(source, shared, release_id=base.release_id, generated_at='2026-11-14T00:00:00Z',
        logical_names=select_index_artifacts(source))
    (old_run / 'current.json').write_bytes((shared / 'current.json').read_bytes())
    publish_literature_candidate(run=old_run, objects_dir=shared / 'objects', papers=base, config=config,
        receipt=tmp_path / 'receipt', client=store)
    literature_projection.save_template_compatibility(base, {'template_fingerprint': 'a' * 64})
    monkeypatch.setattr(literature_projection, '_local_candidate', lambda *args: base)
    snapshot, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-c')])
    run = tmp_path / 'run'; run.mkdir()
    capture_surveillance_baseline(run, config, prefix=RESEARCH_PREFIX, client=store)
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    kwargs = dict(run=run, snapshot=snapshot, stage=stage, shared=shared, receipt=tmp_path / 'receipt',
        release_id='next', config=config, client=store)
    marker = publish(**kwargs, prepare_build=True)
    build['projection_baseline'] = marker['candidate']
    Path(f'{stage}.inputs.json').write_text(json.dumps(build))
    if mismatched_index:
        # Simulate a crash after freezing A, then rebuilding valid B snapshot
        # inputs/collections in the same run before any Papers candidate exists.
        freeze_literature_json(old_snapshot / 'public/research', shared, release_id='next',
            generated_at='2026-11-14T00:00:00Z',
            logical_names=select_index_artifacts(old_snapshot / 'public/research'))
        frozen = (shared / 'current.json').read_bytes()
        (run / 'current.json').write_bytes(frozen)
        writes = len(store.writes)
        with pytest.raises(ValueError, match='Frozen research index does not match'):
            publish(**kwargs, prepare_only=True)
        assert (run / 'current.json').read_bytes() == frozen
        assert len(store.writes) == writes
        assert not (run / 'papers-prepared.json').exists()
        return
    attempts = []

    def verify(candidate):
        attempts.append(candidate.generation)
        if len(attempts) == 1:
            raise ValueError('public verification pending')
        return {'status': 'verified'}

    with pytest.raises(ValueError, match='public verification pending'):
        publish(**kwargs, projection_verifier=verify)
    assert not (run / 'published.json').exists()
    pointer = json.loads(store.objects[f'{RESEARCH_PREFIX}/current.json'])
    assert pointer['release_id'] == 'next'
    assert pointer['files']['index.json']['sha256'] != json.loads((old_run / 'current.json').read_bytes())['files']['index.json']['sha256']
    writes = len(store.writes)
    result = publish(**kwargs, projection_verifier=verify)
    assert len(store.writes) == writes
    assert result['projection']['added_slugs'] == ['paper-c']
    assert result['projection']['deleted_slugs'] == ['paper-a', 'paper-b']
    assert result['json']['pointer_changed'] is False
    assert result['stage_timing']['public_verification_seconds'] >= 0


def test_empty_snapshot_canary_checks_current_three_locale_landing_pages(tmp_path):
    base = initial(tmp_path / 'base')
    _, _, _, static = snapshot_and_collections(tmp_path / 'empty', [])
    empty, _ = prepare_full_snapshot_candidate(base, articles=[], static=static,
        directory=tmp_path / 'empty-v3', release_id='next')

    class Response:
        status = 200
        headers = {'X-GIDS-Research-Release': 'next'}
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return self.url
        def read(self, size):
            return (f'<meta name="gids-research-generation" content="{empty.generation}">'.encode()
                + (empty.root / (self.url.removeprefix(empty.origin + '/') + 'index.html')).read_bytes())

    result = verify_projected_publication(empty, opener=lambda request, **kwargs: Response(request.full_url))
    assert {item['locale'] for item in result['canaries']} == {'en', 'zh', 'fr'}


@pytest.mark.parametrize('local', [True, False])
def test_empty_snapshot_remains_composable_for_a_later_site_release(tmp_path, monkeypatch, local):
    from src.services.data_release import site_research_baseline

    base = initial(tmp_path / 'base')
    _, _, _, static = snapshot_and_collections(tmp_path / 'empty', [])
    empty, _ = prepare_full_snapshot_candidate(base, articles=[], static=static,
        directory=tmp_path / 'empty-v3', release_id='next')
    store = Store()
    result = publish_papers_content(empty, client=store)
    previous = {'release_id': empty.release_id, 'papers': {'papers_origin': empty.origin,
        'papers_generation_sha256': empty.generation, 'papers_manifest_sha256': result['manifest_sha256'],
        'papers_manifest_url': result['manifest_url'], 'papers_file_count': len(empty.entries)}}
    monkeypatch.setattr(site_research_baseline, '_local_candidate', lambda *args: empty if local else None)
    _, _, _, updated_static = snapshot_and_collections(tmp_path / 'updated', [], release_id='updated')
    composed = site_research_baseline.compose_site_candidate(run=tmp_path / 'run', static=updated_static,
        previous=previous, client=store, release_id='updated', directory=tmp_path / 'composed', workers=2)
    assert_v3_candidate(composed)
    assert not any('paper_template' in item for item in composed.entries)
    assert len([item for item in composed.entries if item['key'].startswith(f'{PREFIX}/templates/')]) == 3
    assert len([item for item in composed.entries if item['key'].startswith(f'{PREFIX}/assets/')]) == 1


def test_local_candidate_allows_shared_exports_symlink_but_stays_inside_it(tmp_path, monkeypatch):
    from src.services.data_release import site_research_baseline

    root = tmp_path / 'release-root'
    shared_exports = tmp_path / 'shared-exports'
    root.mkdir()
    candidate_path = shared_exports / 'site-research-runs/old/site-prior/projection-v3/candidate.json'
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text('{}')
    (root / 'exports').symlink_to(shared_exports, target_is_directory=True)
    candidate = SimpleNamespace(release_id='site-prior', origin='https://papers.example',
        generation='a' * 64, manifest=b'captured-manifest')
    monkeypatch.setattr(site_research_baseline, 'load_projection_candidate', lambda _path: candidate)
    monkeypatch.setattr(site_research_baseline, 'assert_v3_candidate', lambda _candidate: None)
    previous = {'release_id': 'site-prior', 'papers': {
        'papers_generation_sha256': candidate.generation,
        'papers_manifest_sha256': hashlib.sha256(candidate.manifest).hexdigest(),
    }}

    assert site_research_baseline._local_candidate(
        root / 'exports/site-research-runs/current/site-current', previous, candidate.origin,
    ) is candidate


def test_full_snapshot_rejects_a_baseline_with_one_missing_article_locale(tmp_path):
    base = initial(tmp_path / 'base')
    base = replace(base, entries=tuple(entry for entry in base.entries
        if not (entry.get('paper_template', {}).get('slug') == 'paper-a'
            and entry['paper_template']['locale'] == 'fr')))
    _, _, _, static = snapshot_and_collections(tmp_path / 'static', [article('paper-a')])
    with pytest.raises(ValueError, match='incomplete article locales'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-a')], static=static,
            directory=tmp_path / 'incomplete', release_id='next')


def test_empty_snapshot_with_no_compatible_template_fails_before_publication(tmp_path, monkeypatch):
    snapshot, stage, build, _ = snapshot_and_collections(tmp_path / 'empty', [])
    # Missing/changed template identity makes the builder choose its full
    # fallback. With zero articles there is no real article shell to extract.
    build.update(scope='full', inputs={'schema': 1, 'shared': 'changed', 'articles': {}}, reused=[])
    Path(f'{stage}.inputs.json').write_text(json.dumps(build))
    run = tmp_path / 'run'; run.mkdir()
    config = replace(CONFIG, public_base_url='https://papers.example')
    store = Store()
    capture_surveillance_baseline(run, config, prefix=RESEARCH_PREFIX, client=store)
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    with pytest.raises(ValueError, match='no article pages'):
        publish(run=run, snapshot=snapshot, stage=stage, shared=tmp_path / 'shared',
            receipt=tmp_path / 'receipt', release_id='next', config=config, client=store)
    assert store.writes == []
    assert not (run / 'published.json').exists()


@pytest.mark.parametrize('assets', [
    {},
    {'_astro/collection.js': 'import "./missing-chunk.js";'},
    {'_astro/collection.js': 'import "./style.css";', '_astro/style.css': '@font-face{src:url(missing-font.woff2)}'},
])
def test_collection_asset_closure_rejects_direct_and_transitive_missing_dependencies(tmp_path, assets):
    base = initial(tmp_path / 'base')
    _, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-c')])
    page = stage / 'research/index.html'
    page.write_text('<html><script type="module" src="/_astro/collection.js"></script></html>')
    build['pages']['research/index.html'] = hashlib.sha256(page.read_bytes()).hexdigest()
    for relative, body in assets.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    manifest = build_research_papers_manifest(stage, origin=base.origin, build=build)
    static = prepare_papers_content_from_manifest(stage, manifest, papers_origin=base.origin, release_id='next')
    with pytest.raises(ValueError, match='asset dependency missing'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-c')], static=static,
            directory=tmp_path / 'incomplete', release_id='next')


def test_collection_asset_closure_ignores_a_quoted_node_runtime_name(tmp_path):
    base = initial(tmp_path / 'base')
    _, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-c')])
    page = stage / 'research/index.html'
    page.write_text('<html><script type="module" src="/_astro/collection.js"></script></html>')
    build['pages']['research/index.html'] = hashlib.sha256(page.read_bytes()).hexdigest()
    for relative, body in {
        '_astro/collection.js': 'import "./style.css"; if (navigator.userAgent.indexOf("Node.js") > -1) {} Yi(a.endsWith(`.png`)?a:`${a}.png`)',
        '_astro/style.css': 'body{}',
    }.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    manifest = build_research_papers_manifest(stage, origin=base.origin, build=build)
    static = prepare_papers_content_from_manifest(stage, manifest, papers_origin=base.origin, release_id='next')
    candidate, _ = prepare_full_snapshot_candidate(base, articles=[article('paper-c')], static=static,
        directory=tmp_path / 'complete', release_id='next')
    assert '_astro/Node.js' not in {item['path'] for item in candidate.entries}
    assert '_astro/style.css' in {item['path'] for item in candidate.entries}


def shell_html(slug, locale):
    return (f'<html><head><title>{slug}</title><meta name="description" content="{slug}">'
            f'<link rel="canonical" href="https://example.test/{slug}">'
            f'<link rel="stylesheet" href="/_astro/shell.abc123.css">'
            f'</head><body><a href="/{slug}">language</a>'
            f'<main id="main-content"><p data-astro-cid-abcd1234>{slug}</p></main>'
            f'<footer>new-footer-{locale}</footer></body></html>')


def write_shell_fixture(stage, build, slug='paper-a'):
    css = stage / '_astro/shell.abc123.css'
    css.parent.mkdir(parents=True, exist_ok=True)
    css.write_text('footer{color:#123}')
    pages = dict(build['pages'])
    for locale, prefix in (('en', ''), ('zh', 'zh/'), ('fr', 'fr/')):
        relative = f'{prefix}research/articles/{slug}/index.html'
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(shell_html(slug, locale))
        pages[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    build.update(pages=pages, scope='shell', article_pages_rendered=3, article_pages_restored=0,
                 shell_slug=slug, template_fingerprint='b' * 64)
    Path(f'{stage}.inputs.json').write_text(json.dumps(build))
    return build


@pytest.mark.parametrize('fresh_shells', [False, True])
def test_full_snapshot_resources_follow_the_selected_shells_without_mutating_the_baseline(tmp_path, fresh_shells):
    root = tmp_path / 'base'
    old_resources = {
        '_astro/old.css': '.skip-link{position:fixed}.evidence-page{display:grid}@font-face{src:url(old.woff2)}',
        '_astro/old.js': 'import "./shared.abc123.js";',
        '_astro/old.woff2': 'old font',
        '_astro/shared.abc123.js': 'export const shared = true;',
    }
    for relative, body in old_resources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    for locale, prefix in (('en', ''), ('zh', 'zh/'), ('fr', 'fr/')):
        path = root / f'{prefix}research/articles/paper-a/index.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(shell_html('paper-a', locale).replace('shell.abc123.css', 'old.css')
            .replace('</head>', '<script type="module" src="/_astro/old.js"></script></head>')
            .replace('<body>', '<body><a class="skip-link">Skip</a>'))
    legacy = prepare_papers_content_from_manifest(root, build_papers_manifest(root, papers_origin='https://papers.example'),
        papers_origin='https://papers.example', release_id='old', template_dir=tmp_path / 'v2')
    base = prepare_projection_candidate(legacy, articles=[article('paper-a')], directory=tmp_path / 'v3',
        release_id='old', migration=True)[0]
    before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.rglob('*') if path.is_file()}
    _, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-a')])
    new_resources = {'_astro/shared.abc123.js': old_resources['_astro/shared.abc123.js']}
    shells = None
    if fresh_shells:
        write_shell_fixture(stage, build)
        new_resources.update({
            '_astro/shell.abc123.css': '.skip-link{position:fixed}.evidence-page{display:grid}@font-face{src:url(new.woff2)}',
            '_astro/new.js': 'import "./shared.abc123.js";',
            '_astro/new.woff2': 'new font',
        })
        for relative in build['pages']:
            if '/articles/' not in relative:
                continue
            path = stage / relative
            path.write_text(path.read_text().replace('<body>', '<body><a class="skip-link">Skip</a>')
                .replace('</head>', '<script type="module" src="/_astro/new.js"></script></head>'))
            build['pages'][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        shells = literature_projection.compile_locale_shells(stage, 'paper-a')
    for relative, body in new_resources.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    static = prepare_papers_content_from_manifest(stage, build_research_papers_manifest(stage, origin=base.origin, build=build),
        papers_origin=base.origin, release_id='next')
    candidate, _ = prepare_full_snapshot_candidate(base, articles=[article('paper-a')], static=static,
        directory=tmp_path / 'next', release_id='next', locale_shells=shells)
    paths = {entry['path'] for entry in candidate.entries}
    retired = set(old_resources) - new_resources.keys()
    assert (paths & retired) == (set() if fresh_shells else retired)
    assert set(new_resources) <= paths
    descriptor = next(entry['paper_template'] for entry in candidate.entries if 'paper_template' in entry)
    manifest = _read_object(candidate, f'{PREFIX}/assets/{descriptor["assets_sha256"]}.json')
    asset_paths = {entry['path'] for entry in manifest['items']}
    assert (asset_paths & retired) == (set() if fresh_shells else retired)
    assert all((stage / path).exists() == (not fresh_shells) for path in retired)
    assert {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()} == before
    # Even a complete shell replacement cannot redefine an existing hashed
    # resource path: the old and new publication identities remain immutable.
    (stage / '_astro/shared.abc123.js').write_text('changed under the same asset name')
    changed = prepare_papers_content_from_manifest(stage, build_research_papers_manifest(stage, origin=base.origin, build=build),
        papers_origin=base.origin, release_id='next')
    with pytest.raises(ValueError, match='immutable asset collision'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-a')], static=changed,
            directory=tmp_path / 'collision', release_id='next', locale_shells=shells)


def test_shell_update_retargets_every_published_paper_without_rewriting_records(tmp_path, monkeypatch):
    base = initial(tmp_path / 'base')
    before = {path.relative_to(base.root): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in base.root.rglob('*') if path.is_file()}
    config = replace(CONFIG, public_base_url=base.origin)
    store = Store()
    old_snapshot, _, _, _ = snapshot_and_collections(tmp_path / 'old', [article('paper-a'), article('paper-b')])
    old_run = tmp_path / 'old-run'
    old_run.mkdir()
    shared = tmp_path / 'shared'
    capture_surveillance_baseline(old_run, config, prefix=RESEARCH_PREFIX, client=store)
    freeze_literature_json(old_snapshot / 'public/research', shared, release_id=base.release_id,
        generated_at='2026-11-15T00:00:00Z', logical_names=select_index_artifacts(old_snapshot / 'public/research'))
    (old_run / 'current.json').write_bytes((shared / 'current.json').read_bytes())
    publish_literature_candidate(run=old_run, objects_dir=shared / 'objects', papers=base, config=config,
        receipt=tmp_path / 'receipt', client=store)
    literature_projection.save_template_compatibility(base, {'template_fingerprint': 'a' * 64})
    monkeypatch.setattr(literature_projection, '_local_candidate', lambda *args: base)
    snapshot, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-a'), article('paper-b')])
    write_shell_fixture(stage, build)
    run = tmp_path / 'run'
    run.mkdir()
    capture_surveillance_baseline(run, config, prefix=RESEARCH_PREFIX, client=store)
    monkeypatch.setenv('GIDS_PAPERS_PROJECTION_V3', '1')
    marker = publish(run=run, snapshot=snapshot, stage=stage, shared=shared, receipt=tmp_path / 'receipt',
        release_id='next', config=config, client=store, prepare_build=True)
    build['projection_baseline'] = marker['candidate']
    Path(f'{stage}.inputs.json').write_text(json.dumps(build))
    publish(run=run, snapshot=snapshot, stage=stage, shared=shared, receipt=tmp_path / 'receipt',
        release_id='next', config=config, client=store, prepare_only=True)
    candidate = load_projection_candidate(run / 'projection-v3/candidate.json')
    old_records = {(entry['paper_template']['slug'], entry['paper_template']['locale']): entry['paper_template']['record_sha256']
                   for entry in base.entries if 'paper_template' in entry}
    old_templates = {(entry['paper_template']['slug'], entry['paper_template']['locale']): entry['paper_template']['template_sha256']
                     for entry in base.entries if 'paper_template' in entry}
    projected = [entry for entry in candidate.entries if 'paper_template' in entry]
    assert {(entry['paper_template']['slug'], entry['paper_template']['locale']) for entry in projected} == {
        (slug, locale) for slug in ('paper-a', 'paper-b') for locale in ('en', 'zh', 'fr')}
    for entry in projected:
        descriptor = entry['paper_template']
        identity = (descriptor['slug'], descriptor['locale'])
        assert descriptor['record_sha256'] == old_records[identity]
        assert descriptor['template_sha256'] != old_templates[identity]
    same_locale = [entry['paper_template']['template_sha256'] for entry in projected if entry['paper_template']['locale'] == 'en']
    assert len(set(same_locale)) == 1
    shell = _read_object(candidate, f"{PREFIX}/templates/{same_locale[0]}.json")
    assert 'new-footer-en' in json.dumps(shell)
    assert '_astro/shell.abc123.css' in {entry['path'] for entry in candidate.entries}
    assert not any('paper_template' not in entry and 'research/articles/' in entry['path'] for entry in candidate.entries)
    compatibility = json.loads((candidate.template_root / 'template-compatibility.json').read_bytes())
    assert compatibility['template_fingerprint'] == 'b' * 64
    assert {path.relative_to(base.root): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in base.root.rglob('*') if path.is_file()} == before
    broken = literature_projection.compile_locale_shells(stage, 'paper-a')
    broken['en']['shell']['chunks'].append('<link rel="stylesheet" href="/_astro/missing.css">')
    with pytest.raises(ValueError, match='outside the retained catalogue'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-a'), article('paper-b')],
            static=prepare_papers_content_from_manifest(stage, build_research_papers_manifest(
                stage, origin=base.origin, build=build), papers_origin=base.origin, release_id='next'),
            directory=tmp_path / 'missing-asset', release_id='next', locale_shells=broken)


def test_collection_asset_closure_checks_complete_dependencies_and_their_hashes(tmp_path):
    base = initial(tmp_path / 'base')
    _, stage, build, _ = snapshot_and_collections(tmp_path / 'new', [article('paper-c')])
    page = stage / 'research/index.html'
    page.write_text('<html><astro-island component-url="/_astro/collection.js"></astro-island></html>')
    build['pages']['research/index.html'] = hashlib.sha256(page.read_bytes()).hexdigest()
    for relative, body in {'_astro/collection.js': 'import "./style.css";',
                           '_astro/style.css': '@font-face{src:url(font.woff2)}',
                           '_astro/font.woff2': 'frozen font'}.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    manifest = build_research_papers_manifest(stage, origin=base.origin, build=build)
    static = prepare_papers_content_from_manifest(stage, manifest, papers_origin=base.origin, release_id='next')
    candidate, _ = prepare_full_snapshot_candidate(base, articles=[article('paper-c')], static=static,
        directory=tmp_path / 'complete', release_id='next')
    assert '_astro/font.woff2' in {item['path'] for item in candidate.entries}
    (stage / '_astro/font.woff2').write_text('changed font')
    with pytest.raises(ValueError, match='asset changed'):
        prepare_full_snapshot_candidate(base, articles=[article('paper-c')], static=static,
            directory=tmp_path / 'changed', release_id='next')
