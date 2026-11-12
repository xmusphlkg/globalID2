import copy
import json
from pathlib import Path

import pytest

from src.services.data_release import papers_cleanup as cleanup
from src.services.data_release.papers_r2 import R2S3Error


def fixture(tmp_path):
    files = []
    inventory = {'papers-manifest.json': {'bytes': 123, 'etag': 'legacy-wire-etag'}}
    for prefix in ('', 'zh/', 'fr/'):
        key = prefix + 'research/articles/paper-a/'
        path = tmp_path / (key + 'index.html')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'old page')
        files.append({'key': key, 'path': key + 'index.html', 'sha256': cleanup.sha(b'old page'), 'bytes': 8})
        inventory[key] = {'bytes': 8, 'etag': 'original-page-etag'}
    files.append({'key': '_astro/keep.css', 'path': '_astro/keep.css', 'sha256': 'a' * 64, 'bytes': 4})
    inventory['_astro/keep.css'] = {'bytes': 4, 'etag': 'asset'}
    inventory['paper-template-v1/records/' + 'c' * 64 + '.json'] = {'bytes': 6, 'etag': 'prototype'}
    inventory['releases/sha256/objects/history/keep'] = {'bytes': 10, 'etag': 'history'}
    manifest = cleanup.json_bytes({'files': files, 'file_count': len(files)})
    plan = cleanup.prepare_plan(manifest, inventory, tmp_path, bucket='test')
    pointer = cleanup.json_bytes({'release_id': 'new-release', 'papers': {'papers_origin': 'https://papers.example'}})
    coverage = {x['key']: {'sha256': x['sha256'], 'bytes': x['bytes']} for x in plan['articles']}
    return plan, manifest, pointer, coverage


class Client:
    def __init__(self, manifest, pointer):
        self.objects = {cleanup.POINTER: pointer, 'papers-manifest.json': manifest}
        self.deleted = []
        self.requests = []
        self.after_delete = None

    def get_object(self, key):
        return self.objects.get(key)

    def _request(self, method, key, **kwargs):
        assert method == 'PUT'
        assert kwargs['conditional_headers']['if-match'] == '"legacy-wire-etag"'
        self.requests.append(key)
        self.objects[key] = kwargs['body']
        return 200, b''

    def delete_objects(self, keys):
        self.deleted.extend(keys)
        if self.after_delete:
            self.after_delete()


class Response:
    status = 200

    def __init__(self, mode='template'):
        self.headers = {'X-GIDS-Paper-Mode': mode, 'X-GIDS-Paper-SHA256': cleanup.sha(b'old page'),
                        'X-GIDS-Research-Release': 'new-release'}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return b'<html>'


def test_plan_only_includes_proven_static_articles_and_orphan_prototype(tmp_path):
    plan, manifest, _, _ = fixture(tmp_path)
    assert len(plan['articles']) == 3
    assert len(plan['prototype']) == 1
    assert plan['stored_bytes'] == 30
    assert all(cleanup.ARTICLE.fullmatch(x['key']) for x in plan['articles'])
    cleanup.validate_plan(plan, manifest)
    (tmp_path / plan['articles'][0]['path']).write_text('modified')
    with pytest.raises(ValueError, match='Changed article backup'):
        cleanup.validate_plan(plan, manifest)


def test_rejects_arbitrary_deletion_keys_and_changed_manifest(tmp_path):
    plan, manifest, _, _ = fixture(tmp_path)
    bad = copy.deepcopy(plan)
    bad['prototype'].append({'key': 'site-data/research/current.json'})
    with pytest.raises(ValueError, match='Unsafe prototype'):
        cleanup.validate_plan(bad, manifest)
    with pytest.raises(ValueError, match='original manifest'):
        cleanup.validate_plan(plan, b'{}')


def test_prunes_manifest_and_deletes_exact_plan_after_canaries(tmp_path, monkeypatch):
    plan, manifest, pointer, coverage = fixture(tmp_path)
    client = Client(manifest, pointer)
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: coverage)
    result = cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
        bucket='test', output=tmp_path / 'receipt', public_opener=lambda *a, **kw: Response())
    assert result['completed'] and result['removed'] == 4
    assert set(client.deleted) == {x['key'] for x in [*plan['articles'], *plan['prototype']]}
    current = json.loads(client.objects['papers-manifest.json'])
    assert [x['key'] for x in current['files']] == ['_astro/keep.css']
    assert current['article_storage'] == 'paper-template-v2'
    assert (tmp_path / 'receipt/legacy-papers-manifest.json').read_bytes() == manifest
    # Retry after partial/complete cleanup is safe: same replacement manifest.
    cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
        bucket='test', output=tmp_path / 'receipt', public_opener=lambda *a, **kw: Response())
    assert len(client.requests) == 1


def test_missing_new_coverage_or_failed_canary_prevents_all_mutations(tmp_path, monkeypatch):
    plan, manifest, pointer, coverage = fixture(tmp_path)
    client = Client(manifest, pointer)
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: {})
    with pytest.raises(ValueError, match='cover every'):
        cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
            bucket='test', output=tmp_path / 'receipt')
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: coverage)
    with pytest.raises(R2S3Error, match='cutover not verified'):
        cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
            bucket='test', output=tmp_path / 'receipt', public_opener=lambda *a, **kw: Response('legacy'))
    assert not client.deleted and not client.requests


def test_pointer_change_stops_remaining_batches(tmp_path, monkeypatch):
    plan, manifest, pointer, coverage = fixture(tmp_path)
    client = Client(manifest, pointer)
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: coverage)
    client.after_delete = lambda: client.objects.update({cleanup.POINTER: b'changed'})
    with pytest.raises(R2S3Error, match='changed during cleanup'):
        cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
            bucket='test', output=tmp_path / 'receipt', public_opener=lambda *a, **kw: Response(), batch_size=1)
    assert len(client.deleted) == 1
    assert json.loads((tmp_path / 'receipt/cleanup-receipt.json').read_bytes())['completed'] is False


@pytest.mark.parametrize('field,value', [('sha256', 'b' * 64), ('bytes', 9)])
def test_changed_article_digest_or_size_prevents_cleanup(tmp_path, monkeypatch, field, value):
    plan, manifest, pointer, coverage = fixture(tmp_path)
    client = Client(manifest, pointer)
    coverage[plan['articles'][0]['key']][field] = value
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: coverage)
    with pytest.raises(ValueError, match='differs from verified original'):
        cleanup.apply_plan(plan, manifest, client, publication_sha256=cleanup.sha(pointer),
            bucket='test', output=tmp_path / 'receipt', public_opener=lambda *a, **kw: Response())
    assert not client.deleted and not client.requests


def test_production_canaries_cover_three_languages_on_every_host(tmp_path):
    plan, _, _, coverage = fixture(tmp_path)
    current = {'release_id': 'new-release', 'papers': {'papers_origin': cleanup.PRODUCTION_ORIGINS[2]}}
    urls = []
    def opener(request, **kwargs):
        assert request.get_header('User-agent').startswith('GlobalIDReleaseVerifier/')
        urls.append(request.full_url)
        return Response()
    cleanup.verify_public_samples(plan, coverage, current, opener=opener)
    assert len(urls) == 9
    assert set(urls) == {origin + '/' + x['key'] for origin in cleanup.PRODUCTION_ORIGINS for x in plan['articles']}
    def bad_main(request, **kwargs):
        return Response('legacy' if request.full_url.startswith(cleanup.PRODUCTION_ORIGINS[0] + '/') else 'template')
    with pytest.raises(R2S3Error, match='cutover not verified'):
        cleanup.verify_public_samples(plan, coverage, current, opener=bad_main)


def test_retry_after_pruning_and_partial_delete_uses_checkpoint(tmp_path, monkeypatch):
    plan, manifest, pointer, coverage = fixture(tmp_path)
    client = Client(manifest, pointer)
    monkeypatch.setattr(cleanup, 'active_article_coverage', lambda *a: coverage)
    calls = 0
    def interrupt():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise R2S3Error('interrupted deletion')
    client.after_delete = interrupt
    options = dict(publication_sha256=cleanup.sha(pointer), bucket='test', output=tmp_path / 'receipt',
                   public_opener=lambda *a, **kw: Response(), batch_size=1)
    with pytest.raises(R2S3Error, match='interrupted deletion'):
        cleanup.apply_plan(plan, manifest, client, **options)
    assert len(client.requests) == 1
    client.after_delete = None
    result = cleanup.apply_plan(plan, manifest, client, **options)
    assert result['completed']
    assert len(client.requests) == 1  # No stale original-manifest ETag on retry.
    with pytest.raises(R2S3Error, match='no matching cleanup checkpoint'):
        cleanup.apply_plan(plan, manifest, client, **{**options, 'output': tmp_path / 'unrelated'})


def test_active_catalogue_requires_hashes_and_template_descriptors(tmp_path):
    key = 'research/articles/paper-a/'
    entry = {'logical_key': key, 'sha256': 'b' * 64,
             'key': 'paper-template-v2/records/' + 'c' * 64 + '.json',
             'paper_template': {'template_sha256': 'd' * 64, 'record_sha256': 'c' * 64,
                                'locale': 'en', 'slug': 'paper-a'}}
    shard = cleanup.json_bytes({'schema_version': 1, 'items': [entry]})
    shard_hash = cleanup.sha(shard)
    shard_key = f'releases/sha256/catalogue-shards/{shard_hash}.json'
    generation = 'a' * 64
    catalogue = cleanup.json_bytes({'schema_version': 2, 'generation_sha256': generation,
        'shards': {'00': {'key': shard_key, 'sha256': shard_hash, 'bytes': len(shard)}}})
    pointer = cleanup.json_bytes({'papers': {'papers_generation_sha256': generation,
                                           'papers_manifest_sha256': cleanup.sha(catalogue)}})
    client = Client(b'{}', pointer)
    client.objects.update({f'releases/sha256/catalogues/{generation}.json': catalogue, shard_key: shard})
    assert cleanup.active_article_coverage(client, pointer) == {key: entry}
    client.objects[shard_key] = b'corrupted'
    with pytest.raises(R2S3Error, match='checksum mismatch'):
        cleanup.active_article_coverage(client, pointer)
