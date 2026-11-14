from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs
from xml.etree import ElementTree

import pytest

from src.services.data_release import papers_gc as gc
from src.services.data_release.papers_cleanup import json_bytes, sha

NOW = datetime(2026, 11, 14, 8, tzinfo=timezone.utc)
OLD = '2026-11-11T08:00:00+00:00'
RECENT = '2026-11-14T00:00:00+00:00'


class Client:
    def __init__(self):
        self.objects = {}
        self.metadata = {}
        self.deleted = []
        self.batches = []
        self.after_delete = None

    def put(self, key, body, age=OLD):
        self.objects[key] = body
        self.metadata[key] = {'bytes': len(body), 'etag': sha(body), 'last_modified': age}
        return key

    def content(self, text, age=OLD):
        body = text.encode()
        return self.put(f'releases/sha256/objects/{sha(body)}/' + 'a' * 16, body, age)

    def catalogue(self, entries, release, age):
        shard = json_bytes({'schema_version': 1, 'items': entries})
        shard_key = self.put(f'releases/sha256/catalogue-shards/{sha(shard)}.json', shard, age)
        basis = {'schema_version': 2, 'release_id': release, 'shards': {'00': {
            'key': shard_key, 'sha256': sha(shard), 'bytes': len(shard), 'count': len(entries)}}}
        generation = sha(json_bytes(basis))
        body = json_bytes({**basis, 'generation_sha256': generation})
        key = self.put(f'releases/sha256/catalogues/{generation}.json', body, age)
        return key, generation, sha(body), shard_key

    def get_object(self, key):
        return self.objects.get(key)

    def _request(self, method, key, **kwargs):
        assert method == 'GET' and key == ''
        prefix = parse_qs(kwargs['query'])['prefix'][0]
        root = ElementTree.Element('ListBucketResult', xmlns='http://s3.amazonaws.com/doc/2006-03-01/')
        ElementTree.SubElement(root, 'IsTruncated').text = 'false'
        for name, meta in sorted(self.metadata.items()):
            if not name.startswith(prefix):
                continue
            item = ElementTree.SubElement(root, 'Contents')
            for field, value in [('Key', name), ('Size', meta['bytes']), ('ETag', '"' + meta['etag'] + '"'), ('LastModified', meta['last_modified'])]:
                ElementTree.SubElement(item, field).text = str(value)
        return 200, ElementTree.tostring(root)

    def delete_objects(self, keys):
        self.batches.append(list(keys))
        self.deleted.extend(keys)
        for key in keys:
            self.objects.pop(key, None)
            self.metadata.pop(key, None)
        if self.after_delete:
            self.after_delete()


def setup():
    client = Client()
    shared = client.content('shared')
    nested = client.content('nested template asset')
    current_only = client.content('current only')
    recent_only = client.content('recent only')
    expired_only = client.content('expired only')
    orphan = client.content('old orphan')
    grace = client.content('recent orphan', RECENT)
    unrelated = client.put('site-data/surveillance/keep.json', b'original observations')
    record_raw, template_raw = b'{"title":"paper"}', b'{"template":"html"}'
    record = client.put(f'paper-template-v3/records/{sha(record_raw)}.json', record_raw)
    template = client.put(f'paper-template-v3/templates/{sha(template_raw)}.json', template_raw)
    assets_raw = json_bytes({'schema': 1, 'items': [{'key': nested, 'sha256': sha(client.objects[nested])}]})
    assets = client.put(f'paper-template-v3/assets/{sha(assets_raw)}.json', assets_raw)
    article = {'key': record, 'paper_template': {'schema': 3, 'record_sha256': sha(record_raw),
               'template_sha256': sha(template_raw), 'assets_sha256': sha(assets_raw)}}
    current = client.catalogue([{'key': shared}, {'key': current_only}, article], 'current', RECENT)
    recent = client.catalogue([{'key': shared}, {'key': recent_only}], 'recent', RECENT)
    expired = client.catalogue([{'key': shared}, {'key': expired_only}], 'expired', OLD)
    client.put(gc.POINTER, json_bytes({'release_id': 'current', 'papers': {
        'papers_generation_sha256': current[1], 'papers_manifest_sha256': current[2]}}), RECENT)
    names = {'shared': shared, 'nested': nested, 'current_only': current_only, 'recent_only': recent_only,
             'expired_only': expired_only, 'orphan': orphan, 'grace': grace, 'unrelated': unrelated,
             'record': record, 'template': template, 'assets': assets, 'current_root': current[0],
             'recent_root': recent[0], 'expired_root': expired[0], 'expired_shard': expired[3], 'current_shard': current[3]}
    return client, names


def plan(client):
    return gc.prepare_cleanup(client, gc.inventory_papers(client), bucket='test', now=NOW)


def test_retention_closure_preserves_shared_recent_current_nested_and_other_channels():
    client, names = setup()
    result = plan(client)
    keys = {item['key'] for item in result['candidates']}
    assert keys == {names[k] for k in ('expired_root', 'expired_shard', 'expired_only', 'orphan')}
    assert not keys & set(result['retained_keys'])
    assert result['grace_excluded'] == 1
    assert names['nested'] in result['current_keys']
    assert result['retained_roots'] == sorted([names['current_root'], names['recent_root']])


def test_current_root_never_expires_and_unknown_ages_never_delete():
    client, names = setup()
    client.metadata[names['current_root']]['last_modified'] = OLD
    client.metadata[names['expired_root']]['last_modified'] = 'unknown'
    client.metadata[names['orphan']]['last_modified'] = 'unknown'
    result = plan(client)
    assert not result['retired_roots'] and not result['candidates']
    assert result['unknown_age_excluded'] == 1


@pytest.mark.parametrize('kind', ['missing_nested_asset', 'corrupt_shard'])
def test_incomplete_or_corrupt_current_closure_prevents_plan(kind):
    client, names = setup()
    if kind == 'missing_nested_asset':
        client.metadata.pop(names['nested'])
        client.objects.pop(names['nested'])
    else:
        client.objects[names['current_shard']] = b'corrupt'
    with pytest.raises(ValueError, match='Missing|corrupt'):
        plan(client)
    assert not client.deleted


def test_changed_object_metadata_invalidates_reviewed_plan(tmp_path):
    client, names = setup()
    reviewed = plan(client)
    client.metadata[names['expired_only']]['etag'] = 'changed'
    with pytest.raises(ValueError, match='plan changed'):
        gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None)
    assert not client.deleted


def test_external_fence_must_be_held_before_any_mutation(tmp_path):
    client, _ = setup()
    reviewed = plan(client)
    def reject():
        raise RuntimeError('queued prepared publisher still exists')
    with pytest.raises(RuntimeError, match='queued prepared'):
        gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=reject)
    assert not client.deleted


@pytest.mark.parametrize('change', ['pointer', 'roots', 'fence'])
def test_concurrent_publication_or_lost_fence_stops_remaining_batches(tmp_path, change):
    client, _ = setup()
    reviewed = plan(client)
    failed = False
    def mutate():
        nonlocal failed
        failed = True
        if change == 'pointer':
            client.objects[gc.POINTER] = b'new pointer'
        elif change == 'roots':
            client.catalogue([], 'concurrent', RECENT)
    def assert_fenced():
        if change == 'fence' and failed:
            raise ValueError('external fence lost')
    client.after_delete = mutate
    with pytest.raises(ValueError, match='pointer changed|roots changed|fence lost'):
        gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=assert_fenced, batch_size=1)
    assert len(client.deleted) == 1
    assert client.deleted[0] == reviewed['retired_roots'][0]


def test_exact_apply_and_completed_rerun_are_idempotent(tmp_path):
    client, names = setup()
    reviewed = plan(client)
    result = gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None, batch_size=2)
    assert result['verified'] and result['completed']
    assert set(client.deleted) == {item['key'] for item in reviewed['candidates']}
    assert client.batches[0] == reviewed['retired_roots']
    assert not set(reviewed['retired_roots']) & {key for batch in client.batches[1:] for key in batch}
    assert names['unrelated'] in client.objects and names['nested'] in client.objects
    previous = list(client.deleted)
    gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None, batch_size=2)
    assert client.deleted == previous


def test_delete_success_before_receipt_crash_resumes_from_prepared_keys(tmp_path):
    client, names = setup()
    reviewed = plan(client)
    def crash():
        raise OSError('crash after remote deletion')
    client.after_delete = crash
    with pytest.raises(OSError, match='crash'):
        gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None, batch_size=1)
    assert client.deleted == [names['expired_root']]
    client.after_delete = None
    result = gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None, batch_size=1)
    assert result['verified'] and len(client.deleted) == len(reviewed['candidates'])


def test_injected_other_channel_candidate_is_rejected(tmp_path):
    client, names = setup()
    reviewed = deepcopy(plan(client))
    reviewed['candidates'].append({'key': names['unrelated'], **client.metadata[names['unrelated']]})
    with pytest.raises(ValueError, match='plan changed'):
        gc.apply_cleanup(reviewed, client, bucket='test', output=tmp_path, assert_fenced=lambda: None)
    assert not client.deleted
