from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from src.services.data_release.papers_r2 import (
    MANIFEST_KEY,
    R2PapersConfig,
    R2S3Client,
    R2S3Error,
    _cache_control_for,
    publish_papers_site,
)

CONFIG = R2PapersConfig(
    account_id="acct",
    bucket="globalid-papers",
    access_key_id="key",
    secret_access_key="secret",
)


class _Response:
    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_bucket_probe_url_matches_signed_canonical_uri() -> None:
    seen = []

    def opener(request, _timeout):
        seen.append(request)
        return _Response()

    assert R2S3Client(CONFIG, opener=opener).head_bucket() is True
    # The signed canonical URI is "/<bucket>"; a trailing slash would make
    # every probe fail with SignatureDoesNotMatch.
    assert seen[0].full_url == (
        "https://acct.r2.cloudflarestorage.com/globalid-papers?max-keys=1"
    )


def test_put_object_sends_cache_control_and_signs_it() -> None:
    seen = []

    def opener(request, _timeout):
        seen.append(request)
        return _Response()

    R2S3Client(CONFIG, opener=opener).put_object(
        "research/articles/a/",
        b"<html></html>",
        content_type="text/html; charset=utf-8",
        cache_control="public, max-age=0, must-revalidate",
    )
    request = seen[0]
    assert request.get_header("Cache-control") == "public, max-age=0, must-revalidate"
    assert "cache-control" in request.get_header("Authorization")


def test_put_object_retries_transient_ssl_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def opener(request, _timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise urllib.error.URLError(
                OSError("UNEXPECTED_EOF_WHILE_READING EOF occurred in violation of protocol")
            )
        return _Response()

    monkeypatch.setattr(
        "src.services.data_release.papers_r2.time.sleep",
        lambda seconds: sleeps.append(float(seconds)),
    )
    R2S3Client(CONFIG, opener=opener).put_object("research/articles/a/", b"<html></html>")
    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]


def test_put_object_gives_up_after_transport_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.services.data_release.papers_r2.time.sleep", lambda _seconds: None)

    def opener(request, _timeout):
        raise urllib.error.URLError(OSError("UNEXPECTED_EOF_WHILE_READING"))

    with pytest.raises(R2S3Error, match="transport failed"):
        R2S3Client(CONFIG, opener=opener).put_object("research/articles/a/", b"x")


def test_get_object_treats_http_404_as_missing() -> None:
    def opener(request, _timeout):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, io.BytesIO(b"<Error/>")
        )

    assert R2S3Client(CONFIG, opener=opener).get_object("missing") is None


def test_get_object_raises_on_other_http_errors() -> None:
    def opener(request, _timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b"denied")
        )

    with pytest.raises(R2S3Error) as raised:
        R2S3Client(CONFIG, opener=opener).get_object("secret")
    assert raised.value.status == 403


def test_cache_control_policy() -> None:
    assert _cache_control_for(MANIFEST_KEY) == "no-store, max-age=0"
    assert _cache_control_for("research/articles/a/index.html") == (
        "public, max-age=0, must-revalidate"
    )
    assert "max-age=300" in _cache_control_for("site-data/research/x.json")
    assert "immutable" not in _cache_control_for("research/cards/a.png")


def test_empty_local_manifest_never_wipes_the_bucket(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / MANIFEST_KEY).write_text(
        json.dumps({"schema_version": 1, "files": []}), encoding="utf-8"
    )
    deleted: list[str] = []

    class Remote:
        def get_object(self, key: str) -> bytes | None:
            if key == MANIFEST_KEY:
                return json.dumps(
                    {"files": [{"key": "research/articles/a/", "bytes": 1, "sha256": "x"}]}
                ).encode()
            return None

        def put_object(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("nothing should be uploaded")

        def delete_object(self, key: str) -> None:
            deleted.append(key)

    with pytest.raises(R2S3Error, match="empty"):
        publish_papers_site(papers, CONFIG, client=Remote(), verify_public=False)
    assert deleted == []
