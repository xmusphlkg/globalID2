import gzip
import json

import httpx
import pytest

from src.services.data_release.papers_r2 import (
    MANIFEST_KEY,
    R2PapersConfig,
    R2S3Client,
    R2S3Error,
)

CONFIG = R2PapersConfig(
    account_id="acct", bucket="papers", access_key_id="key", secret_access_key="secret"
)


class Response:
    status = 200

    def __init__(self, body=b""):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.body


def test_compressed_manifest_signs_encoded_payload_and_decodes_legacy_transports():
    seen = []
    body = json.dumps(
        {"files": [{"key": "path/" + str(n), "sha256": "a" * 64} for n in range(100)]}
    ).encode()

    def opener(request, timeout):
        seen.append(request)
        return Response(seen[0].data if request.method == "GET" else b"")

    client = R2S3Client(CONFIG, opener=opener)
    client.put_object(
        MANIFEST_KEY, body, content_type="application/json", cache_control="no-store"
    )
    request = seen[0]
    assert len(request.data) < len(body) / 3
    assert gzip.decompress(request.data) == body
    assert request.get_header("Content-encoding") == "gzip"
    assert "content-encoding" in request.get_header("Authorization")
    assert int(request.get_header("Content-length")) == len(request.data)
    assert client.get_object(MANIFEST_KEY) == body


def test_httpx_manifest_is_not_decoded_twice():
    body = b'{"files":[]}'
    client = R2S3Client(CONFIG)
    client._http.close()
    client._http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=gzip.compress(body), headers={"Content-Encoding": "gzip"}
            )
        )
    )
    try:
        assert client.get_object(MANIFEST_KEY) == body
    finally:
        client.close()


def test_ordinary_object_bytes_are_not_changed():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response()

    client = R2S3Client(CONFIG, opener=opener)
    body = b"x" * 2000
    client.put_object("site-data/index.json", body)
    assert seen[0].data == body
    assert seen[0].get_header("Content-encoding") is None


def test_invalid_compressed_manifest_is_terminal():
    client = R2S3Client(CONFIG, opener=lambda *args: Response(b"\x1f\x8btruncated"))
    with pytest.raises(R2S3Error, match="Invalid compressed"):
        client.get_object(MANIFEST_KEY)
