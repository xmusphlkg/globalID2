import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_legacy_copy import plan_verified_legacy_copies, copy_verified_legacy_objects
from src.services.data_release.papers_r2 import R2S3Client, R2S3Error
from tests.unit.test_papers_content import make_candidate, make_template_candidate
from tests.unit.test_surveillance_publish import CONFIG


def md5(body):
    return hashlib.md5(body, usedforsecurity=False).hexdigest()


def setup(tmp_path, compressed=False, duplicate=False):
    content = 'body { color: blue; }' * 200
    files = {'style.css': content}
    if duplicate:
        files['other.css'] = content
    candidate = make_candidate(tmp_path / 'source', files)
    manifest = build_papers_manifest(candidate.root, papers_origin=candidate.origin)
    raw = content.encode()
    wire = gzip.compress(raw, compresslevel=6, mtime=0) if compressed else raw
    inventory = {entry['logical_key']: {'bytes': len(wire), 'etag': md5(wire)} for entry in candidate.entries}
    return candidate, manifest, inventory, raw, wire


class Client:
    config = SimpleNamespace(bucket='test')

    def __init__(self, candidate, wire, compressed=False):
        self.objects = {entry['logical_key']: wire for entry in candidate.entries}
        self.encodings = {key: compressed for key in self.objects}
        self.calls = []
        self.result = None

    def get_object(self, key):
        body = self.objects.get(key)
        return gzip.decompress(body) if body is not None and self.encodings.get(key) else body

    def _request(self, method, key, *, conditional_headers):
        self.calls.append((method, key, conditional_headers))
        source = conditional_headers['x-amz-copy-source'].removeprefix('/test/')
        assert conditional_headers['cf-copy-destination-if-none-match'] == '*'
        assert conditional_headers['x-amz-metadata-directive'] == 'COPY'
        if key in self.objects or conditional_headers['x-amz-copy-source-if-match'] != f'"{md5(self.objects[source])}"':
            raise R2S3Error('precondition', status=412)
        if self.result is not None:
            return self.result
        self.objects[key] = self.objects[source]
        self.encodings[key] = self.encodings[source]
        return 200, f'<CopyObjectResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><ETag>"{md5(self.objects[source])}"</ETag></CopyObjectResult>'.encode()


@pytest.mark.parametrize('compressed', [False, True])
def test_copy_proves_source_and_preserves_wire_encoding_without_body_upload(tmp_path, compressed):
    candidate, manifest, inventory, raw, wire = setup(tmp_path, compressed=compressed)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    assert len(plan.copies) == 1 and plan.raw_bytes == len(raw) and plan.stored_bytes == len(wire)
    client = Client(candidate, wire, compressed)
    proven = copy_verified_legacy_objects(candidate, manifest, inventory, client, plan=plan, workers=2)
    entry = candidate.entries[0]
    assert proven == {entry['key']}
    assert client.objects[entry['key']] == wire
    assert client.get_object(entry['key']) == raw
    assert client.calls[0][2]['x-amz-copy-source-if-match'] == f'"{md5(wire)}"'


def test_plan_rejects_unproven_wire_or_decoded_identity_and_known_destinations(tmp_path):
    candidate, manifest, inventory, _, _ = setup(tmp_path)
    assert not plan_verified_legacy_copies(candidate, manifest, inventory, known=candidate.object_keys).copies
    bad = json.loads(json.dumps(manifest))
    bad['files'][0]['sha256'] = '0' * 64
    assert not plan_verified_legacy_copies(candidate, bad, inventory).copies
    inventory['style.css']['etag'] = '0' * 32
    assert not plan_verified_legacy_copies(candidate, manifest, inventory).copies


def test_identical_sources_are_copied_once(tmp_path):
    candidate, manifest, inventory, _, wire = setup(tmp_path, duplicate=True)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    assert len(plan.copies) == 1
    client = Client(candidate, wire)
    assert len(copy_verified_legacy_objects(candidate, manifest, inventory, client, plan=plan)) == 1
    assert len(client.calls) == 1


def test_source_change_fails_condition_and_leaves_destination_for_local_upload(tmp_path):
    candidate, manifest, inventory, _, wire = setup(tmp_path)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    client = Client(candidate, wire)
    client.objects['style.css'] = b'changed source'
    assert copy_verified_legacy_objects(candidate, manifest, inventory, client, plan=plan) == set()
    assert candidate.entries[0]['key'] not in client.objects


def test_existing_destination_is_verified_and_never_overwritten(tmp_path):
    candidate, manifest, inventory, raw, wire = setup(tmp_path, compressed=True)
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    client = Client(candidate, wire, True)
    key = candidate.entries[0]['key']
    client.objects[key] = raw
    client.encodings[key] = False
    assert copy_verified_legacy_objects(candidate, manifest, inventory, client, plan=plan) == {key}
    assert client.objects[key] == raw
    client.objects[key] = b'conflict'
    with pytest.raises(R2S3Error, match='destination conflict'):
        copy_verified_legacy_objects(candidate, manifest, inventory, client, plan=plan)
    assert client.objects[key] == b'conflict'


@pytest.mark.parametrize('payload', [b'<Error><Code>InternalError</Code></Error>',
                                    b'<CopyObjectResult><ETag>"wrong"</ETag></CopyObjectResult>', b'not XML'])
def test_error_body_or_mismatched_etag_never_proves_destination(tmp_path, payload):
    candidate, manifest, inventory, _, wire = setup(tmp_path)
    client = Client(candidate, wire)
    client.result = (200, payload)
    with pytest.raises(R2S3Error):
        copy_verified_legacy_objects(candidate, manifest, inventory, client)


def test_plan_does_not_copy_template_article_html(tmp_path):
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    manifest = build_papers_manifest(candidate.root, papers_origin=candidate.origin)
    inventory = {entry['key']: {'bytes': entry['bytes'], 'etag': md5((candidate.root / entry['path']).read_bytes())}
                 for entry in manifest['files']}
    plan = plan_verified_legacy_copies(candidate, manifest, inventory)
    assert [item.source for item in plan.copies] == ['style.css']


def test_copy_headers_are_signed_and_other_arbitrary_headers_rejected():
    client = R2S3Client(CONFIG)
    try:
        request = client._signed_request('PUT', 'target', conditional_headers={
            'x-amz-copy-source': '/test/source', 'x-amz-copy-source-if-match': '"abc"',
            'cf-copy-destination-if-none-match': '*', 'x-amz-metadata-directive': 'COPY'})
        auth = request.get_header('Authorization')
        assert 'cf-copy-destination-if-none-match' in auth
        assert 'x-amz-copy-source-if-match' in auth
        assert request.get_header('X-amz-copy-source') == '/test/source'
        with pytest.raises(ValueError, match='Unsupported'):
            client._signed_request('PUT', 'target', conditional_headers={'x-unsafe': 'value'})
    finally:
        client.close()
