import asyncio
from dataclasses import replace
import gzip
import hashlib
import json
from urllib.parse import unquote

import httpx
import pytest

from src.services.data_release.papers_async_stage import copy_verified_legacy_objects_async, stage_papers_payloads_async
from src.services.data_release.papers_legacy_copy import plan_verified_legacy_copies
from src.services.data_release.papers_r2 import R2S3Client, R2S3Error
from tests.unit.test_papers_content import make_candidate, make_template_candidate
from tests.unit.test_papers_legacy_copy import setup, md5
from tests.unit.test_surveillance_publish import CONFIG


class AsyncStore:
    def __init__(self):
        self.objects, self.encodings, self.calls = {}, {}, []
        self.failures = []
        self.copy_result = None
        self.active = self.maximum = 0

    async def handle(self, request):
        self.active += 1
        self.maximum = max(self.active, self.maximum)
        try:
            await asyncio.sleep(0.001)
            key = unquote(request.url.path).removeprefix('/bucket/')
            self.calls.append((request.method, key))
            if self.failures:
                failure = self.failures.pop(0)
                if failure == 'timeout':
                    raise httpx.ReadTimeout('temporary', request=request)
                return httpx.Response(failure, content=b'busy')
            if request.method == 'GET':
                if key not in self.objects:
                    return httpx.Response(404)
                headers = {'content-encoding': 'gzip'} if self.encodings.get(key) == 'gzip' else {}
                return httpx.Response(200, content=self.objects[key], headers=headers)
            assert request.method == 'PUT'
            if 'x-amz-copy-source' in request.headers:
                source = unquote(request.headers['x-amz-copy-source']).removeprefix('/bucket/')
                assert request.headers['cf-copy-destination-if-none-match'] == '*'
                assert request.headers['x-amz-metadata-directive'] == 'COPY'
                if source not in self.objects:
                    return httpx.Response(404)
                if key in self.objects or request.headers['x-amz-copy-source-if-match'] != f'"{md5(self.objects[source])}"':
                    return httpx.Response(412)
                if self.copy_result is not None:
                    return httpx.Response(200, content=self.copy_result)
                self.objects[key] = self.objects[source]
                self.encodings[key] = self.encodings.get(source)
                return httpx.Response(200, content=f'<CopyObjectResult><ETag>"{md5(self.objects[key])}"</ETag></CopyObjectResult>'.encode())
            assert request.headers['if-none-match'] == '*'
            if key in self.objects:
                return httpx.Response(412)
            self.objects[key] = await request.aread()
            self.encodings[key] = request.headers.get('content-encoding')
            return httpx.Response(200)
        finally:
            self.active -= 1


@pytest.fixture
def signer():
    client = R2S3Client(CONFIG)
    yield client
    client.close()


def test_async_copy_retries_transient_failures_and_verifies_existing_destination(tmp_path, signer):
    candidate, manifest, inventory, raw, wire = setup(tmp_path, compressed=True)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    store = AsyncStore()
    store.objects['style.css'], store.encodings['style.css'] = wire, 'gzip'
    store.failures = [503, 429, 'timeout']
    transport = httpx.MockTransport(store.handle)
    result = copy_verified_legacy_objects_async(candidate, plan, signer, transport=transport, retry_delay=0)
    key = candidate.entries[0]['key']
    assert result == {key} and store.objects[key] == wire
    assert len(store.calls) == 4
    assert copy_verified_legacy_objects_async(candidate, plan, signer, transport=transport, retry_delay=0) == {key}
    assert store.calls[-1] == ('GET', key)
    assert gzip.decompress(store.objects[key]) == raw


def test_async_copy_source_change_and_conflicting_destination_are_not_proven(tmp_path, signer):
    candidate, manifest, inventory, _, _ = setup(tmp_path)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    store = AsyncStore()
    store.objects['style.css'] = b'changed'
    transport = httpx.MockTransport(store.handle)
    assert copy_verified_legacy_objects_async(candidate, plan, signer, transport=transport) == set()
    key = candidate.entries[0]['key']
    store.objects[key] = b'conflict'
    with pytest.raises(R2S3Error, match='destination conflict'):
        copy_verified_legacy_objects_async(candidate, plan, signer, transport=transport)
    assert store.objects[key] == b'conflict'


@pytest.mark.parametrize('response', [b'<Error/>', b'<CopyObjectResult><ETag>"wrong"</ETag></CopyObjectResult>', b'invalid'])
def test_async_copy_does_not_accept_error_or_wrong_etag_xml(tmp_path, signer, response):
    candidate, manifest, inventory, _, wire = setup(tmp_path)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    store = AsyncStore()
    store.objects['style.css'] = wire
    store.copy_result = response
    with pytest.raises(R2S3Error):
        copy_verified_legacy_objects_async(candidate, plan, signer, transport=httpx.MockTransport(store.handle))


def test_async_payload_staging_is_bounded_and_never_publishes_catalogue_or_pointer(tmp_path, signer):
    candidate = make_candidate(tmp_path / 'site', {f'{n}.json': json.dumps({'n': n, 'data': 'repeat' * 1000}) for n in range(25)})
    store = AsyncStore()
    checkpoint = tmp_path / 'checkpoint.json'
    result = stage_papers_payloads_async(candidate, signer, workers=4, checkpoint=checkpoint,
                                         transport=httpx.MockTransport(store.handle))
    assert result == candidate.object_keys == set(store.objects)
    assert 1 < store.maximum <= 4
    assert candidate.manifest_key not in store.objects
    assert not any(key.endswith('current.json') for key in store.objects)
    for entry in candidate.entries:
        assert store.encodings[entry['key']] == 'gzip'
        assert hashlib.sha256(gzip.decompress(store.objects[entry['key']])).hexdigest() == entry['sha256']
    saved = json.loads(checkpoint.read_bytes())
    assert saved['generation'] == candidate.generation
    assert set(saved['proven_keys']) == candidate.object_keys
    calls = len(store.calls)
    assert stage_papers_payloads_async(candidate, signer, known=result, checkpoint=checkpoint,
        transport=httpx.MockTransport(store.handle)) == set()
    assert len(store.calls) == calls
    assert set(json.loads(checkpoint.read_bytes())['proven_keys']) == result


def test_async_payload_existing_conflict_is_rejected(tmp_path, signer):
    candidate = make_candidate(tmp_path / 'site', {'style.css': 'original'})
    store = AsyncStore()
    key = candidate.entries[0]['key']
    store.objects[key] = b'not-original'
    with pytest.raises(R2S3Error, match='destination conflict'):
        stage_papers_payloads_async(candidate, signer, transport=httpx.MockTransport(store.handle))
    assert store.objects[key] == b'not-original'


def test_async_template_payloads_stage_combined_records_once_and_no_html(tmp_path, signer):
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    store = AsyncStore()
    assert stage_papers_payloads_async(candidate, signer, transport=httpx.MockTransport(store.handle)) == candidate.object_keys
    assert sum('/records/' in key for key in store.objects) == 2
    assert not any('/research/articles/' in key for key in store.objects)


def test_async_payload_source_change_fails_before_any_request(tmp_path, signer):
    candidate = make_candidate(tmp_path / 'site', {'style.css': 'original'})
    (candidate.root / 'style.css').write_text('changed')
    store = AsyncStore()
    with pytest.raises(ValueError, match='source changed'):
        stage_papers_payloads_async(candidate, signer, transport=httpx.MockTransport(store.handle))
    assert not store.calls


def test_async_copy_rejects_invalid_wire_descriptor_before_request(tmp_path, signer):
    candidate, manifest, inventory, _, _ = setup(tmp_path)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    store = AsyncStore()
    for fields in ({'etag': 'not-an-etag'}, {'stored_bytes': -1}):
        invalid = replace(plan, copies=(replace(plan.copies[0], **fields),))
        with pytest.raises(ValueError, match='does not match candidate'):
            copy_verified_legacy_objects_async(candidate, invalid, signer, transport=httpx.MockTransport(store.handle))
    assert not store.calls


def test_async_failure_preserves_successful_payload_checkpoint(tmp_path, signer):
    candidate = make_candidate(tmp_path / 'site', {'a.css': 'one', 'b.css': 'two'})
    store = AsyncStore()
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        if count == 2:
            return httpx.Response(403)
        return await store.handle(request)
    checkpoint = tmp_path / 'checkpoint.json'
    with pytest.raises(R2S3Error, match='HTTP 403'):
        stage_papers_payloads_async(candidate, signer, workers=1, checkpoint=checkpoint,
                                    transport=httpx.MockTransport(handler))
    saved = json.loads(checkpoint.read_bytes())
    assert saved['generation'] == candidate.generation
    assert len(saved['proven_keys']) == saved['completed'] == 1
    assert set(saved['proven_keys']) == set(store.objects)
