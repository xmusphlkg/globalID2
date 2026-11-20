"""Exercise durable R2 receipts against a complete in-memory S3 HTTP boundary."""
from __future__ import annotations

import gzip
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from xml.etree import ElementTree

import httpx
import pytest

from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from src.services.data_release.r2_object_index import (
    R2ObjectIndex, R2ReservationConflict, R2StorageBudgetExceeded,
)

CONFIG = R2PapersConfig(account_id='test', bucket='papers', access_key_id='test-key',
                      secret_access_key='test-secret')
IMMUTABLE = 'public, max-age=31536000, immutable'
TYPE = 'application/json'


def digest(body: bytes) -> str:
    return hashlib.md5(body, usedforsecurity=False).hexdigest()


def key_for(body: bytes) -> str:
    return f'paper-template-v3/records/{hashlib.sha256(body).hexdigest()}.json'


class Remote:
    def __init__(self):
        self.objects = {}
        self.requests = []
        self.before_put = None
        self.delete_failures = set()

    def store(self, key, body, *, content_type=TYPE, encoding=None, cache_control=IMMUTABLE):
        self.objects[key] = {'body': body, 'content_type': content_type,
                             'encoding': encoding, 'cache_control': cache_control}

    def headers(self, key):
        item = self.objects[key]
        headers = {'content-length': str(len(item['body'])), 'etag': f'"{digest(item["body"])}"'}
        for name, field in [('content-type', 'content_type'), ('content-encoding', 'encoding'),
                            ('cache-control', 'cache_control')]:
            if item[field] is not None:
                headers[name] = item[field]
        return headers

    def respond(self, request):
        key = request.url.path.removeprefix(f'/{CONFIG.bucket}').lstrip('/')
        self.requests.append((request.method, key, dict(request.headers)))
        if request.method == 'GET' and not key:
            root = ElementTree.Element('ListBucketResult')
            for name, item in sorted(self.objects.items()):
                entry = ElementTree.SubElement(root, 'Contents')
                for field, value in [('Key', name), ('Size', str(len(item['body']))),
                                      ('ETag', f'"{digest(item["body"])}"')]:
                    ElementTree.SubElement(entry, field).text = value
            ElementTree.SubElement(root, 'IsTruncated').text = 'false'
            return httpx.Response(200, content=ElementTree.tostring(root))
        if request.method in {'HEAD', 'GET'}:
            if key not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, headers=self.headers(key),
                                  content=self.objects[key]['body'] if request.method == 'GET' else b'')
        if request.method == 'PUT':
            if self.before_put:
                self.before_put(request, key)
            current = self.objects.get(key)
            if request.headers.get('if-none-match') == '*' and current is not None:
                return httpx.Response(412)
            if 'if-match' in request.headers and (
                    current is None or request.headers['if-match'] != f'"{digest(current["body"])}"'):
                return httpx.Response(412)
            self.store(key, request.content, content_type=request.headers.get('content-type'),
                       encoding=request.headers.get('content-encoding'),
                       cache_control=request.headers.get('cache-control'))
            return httpx.Response(200, headers={'etag': f'"{digest(request.content)}"'})
        if request.method == 'DELETE':
            self.objects.pop(key, None)
            return httpx.Response(204)
        if request.method == 'POST' and not key and 'delete' in request.url.params:
            root = ElementTree.Element('DeleteResult')
            for entry in ElementTree.fromstring(request.content).findall('{*}Object'):
                name = entry.findtext('{*}Key')
                if name in self.delete_failures:
                    error = ElementTree.SubElement(root, 'Error')
                    ElementTree.SubElement(error, 'Key').text = name
                    ElementTree.SubElement(error, 'Code').text = 'AccessDenied'
                else:
                    self.objects.pop(name, None)
            return httpx.Response(200, content=ElementTree.tostring(root))
        pytest.fail(f'Unexpected mock R2 request: {request.method} {key}')

    def operations(self):
        return [(method, key) for method, key, _headers in self.requests]


@pytest.fixture
def make_client(tmp_path):
    opened = []

    def create(remote, *, budget=1_000_000):
        index = R2ObjectIndex(CONFIG.s3_endpoint, CONFIG.bucket, path=tmp_path / 'objects.sqlite3')
        client = R2S3Client(CONFIG, object_index=index, max_total_bytes=budget)
        if client._http is not None:
            client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(remote.respond))
        opened.append((client, index))
        return client, index

    yield create
    for client, index in opened:
        client.close()
        # A restart test may have deliberately closed an earlier connection.
        index.close()


def put(client, key, body, **kwargs):
    return client.put_object_conditional(key, body, previous=None, content_type=TYPE,
                                         cache_control=IMMUTABLE, **kwargs)


def test_confirmed_immutable_upload_survives_process_restart_without_requests(make_client):
    remote, body = Remote(), b'{"paper":1}'
    key = key_for(body)
    first, index = make_client(remote)
    assert put(first, key, body) is True
    assert index.total_bytes() == len(body)
    assert index.pending_bytes() == 0
    first.close()
    index.close()
    remote.requests.clear()
    restarted, _ = make_client(remote)
    assert put(restarted, key, body) is False
    assert remote.requests == []


def test_inventory_only_object_uses_one_head_then_no_requests(make_client):
    remote, body = Remote(), b'{"paper":1}'
    key = key_for(body)
    remote.store(key, body)
    client, index = make_client(remote)
    client.refresh_inventory()
    assert index.get(key)['metadata_known'] is False
    remote.requests.clear()
    assert put(client, key, body) is False
    assert remote.operations() == [('HEAD', key)]
    remote.requests.clear()
    assert put(client, key, body) is False
    assert remote.requests == []


def test_conflicting_immutable_content_fails_without_swallowable_412(make_client):
    remote, body = Remote(), b'{"paper":"expected"}'
    key = key_for(body)
    remote.store(key, b'{"paper":"corrupt"}')
    client, _ = make_client(remote)
    with pytest.raises(R2S3Error) as error:
        put(client, key, body)
    assert error.value.status != 412
    assert not any(method == 'PUT' for method, _key, _headers in remote.requests)
    assert remote.objects[key]['body'] == b'{"paper":"corrupt"}'


def test_mutable_pointer_always_reaches_conditional_remote_put(make_client):
    remote, body = Remote(), b'{"release":"one"}'
    key = 'site-data/research-publication/current.json'
    client, _ = make_client(remote)
    assert client.put_object_conditional(key, body, previous=None) is True
    remote.requests.clear()
    assert client.put_object_conditional(key, body, previous=body) is True
    assert ('PUT', key) in remote.operations()
    sent = next(headers for method, name, headers in remote.requests if method == 'PUT' and name == key)
    assert sent['if-match'] == f'"{digest(body)}"'


def test_capacity_guard_precedes_any_put(make_client):
    remote = Remote()
    remote.store('other-channel/object', b'x' * 9)
    client, index = make_client(remote, budget=10)
    body = b'ab'
    with pytest.raises(R2StorageBudgetExceeded):
        put(client, key_for(body), body)
    assert index.total_bytes() == 9
    assert index.pending_bytes() == 0
    assert not any(method == 'PUT' for method, _key, _headers in remote.requests)


def test_successful_delete_invalidates_receipt_and_reupload_restores_object(make_client):
    remote, body = Remote(), b'content'
    key = key_for(body)
    client, index = make_client(remote)
    put(client, key, body)
    client.delete_object(key)
    assert index.get(key) is None and index.total_bytes() == 0
    remote.requests.clear()
    assert put(client, key, body) is True
    assert remote.objects[key]['body'] == body
    assert ('PUT', key) in remote.operations()


def test_partial_batch_delete_invalidates_all_attempted_receipts(make_client):
    remote, first_body, second_body = Remote(), b'first', b'second'
    first_key, second_key = key_for(first_body), key_for(second_body)
    client, index = make_client(remote)
    put(client, first_key, first_body)
    put(client, second_key, second_body)
    remote.delete_failures.add(second_key)
    with pytest.raises(R2S3Error, match='batch deletion failed'):
        client.delete_objects([first_key, second_key])
    assert first_key not in remote.objects and second_key in remote.objects
    assert index.get(first_key) is None and index.get(second_key) is None
    assert not index.inventory_is_fresh()
    remote.requests.clear()
    assert put(client, first_key, first_body) is True
    assert put(client, second_key, second_body) is False
    assert [key for method, key, _headers in remote.requests if method == 'PUT'] == [first_key]
    assert index.total_bytes() == len(first_body) + len(second_body)


def test_cas_conflict_resolves_remote_size_without_losing_capacity(make_client):
    remote, before, competing = Remote(), b'old', b'other publisher made this object larger'
    key = 'site-data/research-publication/current.json'
    remote.store(key, before, cache_control='no-store')
    client, index = make_client(remote)
    client.refresh_inventory()

    def race(_request, name):
        remote.store(name, competing, cache_control='no-store')
        remote.before_put = None

    remote.before_put = race
    with pytest.raises(R2S3Error) as error:
        client.put_object_conditional(key, b'ours', previous=before)
    assert error.value.status == 412
    assert index.pending_bytes() == 0
    assert index.total_bytes() == len(competing)
    assert index.get(key)['etag'] == digest(competing)


def test_ambiguous_failed_put_preserves_reservation_across_restart(make_client, monkeypatch):
    monkeypatch.setattr('src.services.data_release.papers_r2.time.sleep', lambda _seconds: None)
    remote, body = Remote(), b'uncertain upload'
    key = key_for(body)

    def timeout(request, _key):
        raise httpx.ReadTimeout('Response lost after upload', request=request)

    remote.before_put = timeout
    client, index = make_client(remote)
    with pytest.raises(R2S3Error, match='transport failed'):
        put(client, key, body)
    assert index.get(key) is None
    assert index.pending_bytes() == len(body)
    client.close()
    index.close()
    remote.before_put = None
    restarted, reopened = make_client(remote)
    assert reopened.pending_bytes() == len(body)
    remote.requests.clear()
    with pytest.raises(R2ReservationConflict):
        put(restarted, key, body)
    assert not any(method == 'PUT' for method, _key, _headers in remote.requests)


def test_inventory_refresh_notices_external_deletion_and_repairs_object(make_client):
    remote, body = Remote(), b'content'
    key = key_for(body)
    client, index = make_client(remote)
    put(client, key, body)
    del remote.objects[key]
    client.refresh_inventory(force=True)
    assert index.get(key) is None
    remote.requests.clear()
    assert put(client, key, body) is True
    assert remote.objects[key]['body'] == body


def test_existing_raw_representation_is_verified_then_reused_for_gzip_candidate(make_client):
    remote, body = Remote(), b'{"article":"' + b'long repeated content ' * 200 + b'"}'
    key = key_for(body)
    remote.store(key, body)
    client, index = make_client(remote)
    compressed = gzip.compress(body, mtime=0)
    assert put(client, key, compressed, content_encoding='gzip') is False
    assert not any(method == 'PUT' for method, _key, _headers in remote.requests)
    assert index.total_bytes() == len(body)
    assert index.get(key)['content_sha256'] == hashlib.sha256(body).hexdigest()
    remote.requests.clear()
    assert put(client, key, compressed, content_encoding='gzip') is False
    assert remote.requests == []


@pytest.mark.parametrize('endpoint,bucket', [(CONFIG.s3_endpoint, 'another-bucket'),
                                          ('https://another-account.example', CONFIG.bucket)])
def test_injected_index_must_belong_to_the_client_target(tmp_path, endpoint, bucket):
    index = R2ObjectIndex(endpoint, bucket, path=tmp_path / 'wrong-target.sqlite3')
    created = None
    try:
        with pytest.raises(ValueError, match='target|bucket|endpoint'):
            created = R2S3Client(CONFIG, object_index=index)
    finally:
        if created is not None:
            created.close()
        index.close()


def test_delete_cannot_race_successful_put_before_its_index_commit(make_client, monkeypatch):
    remote, body = Remote(), b'concurrent upload'
    key = key_for(body)
    client, index = make_client(remote)
    commit_started, allow_commit, delete_started, deleted = Event(), Event(), Event(), Event()
    original_commit, original_respond = index.commit, remote.respond

    def delayed_commit(*args, **kwargs):
        commit_started.set()
        assert allow_commit.wait(5), 'Timed out waiting for test to release upload commit'
        return original_commit(*args, **kwargs)

    def respond(request):
        if request.method == 'DELETE':
            deleted.set()
        return original_respond(request)

    def delete():
        delete_started.set()
        client.delete_object(key)

    if client._http is not None:
        client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(index, 'commit', delayed_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        uploading = pool.submit(put, client, key, body)
        assert commit_started.wait(5)
        deleting = pool.submit(delete)
        assert delete_started.wait(5)
        try:
            assert not deleted.wait(0.2), 'DELETE raced an uncommitted upload receipt'
        finally:
            allow_commit.set()
        assert uploading.result(timeout=5) is True
        deleting.result(timeout=5)
    assert key not in remote.objects
    assert index.get(key) is None
    assert index.pending_bytes() == 0
