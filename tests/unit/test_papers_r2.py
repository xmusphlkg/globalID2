from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Self

import pytest

from src.services.data_release import papers_r2
from src.services.data_release.papers_bundle import split_papers_bundle
from src.services.data_release.papers_r2 import (
    R2PapersConfig,
    R2S3Error,
    derive_r2_credentials_from_api_token,
    publish_papers_site,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_derive_r2_credentials_uses_token_id_and_sha256(monkeypatch) -> None:
    monkeypatch.setattr(papers_r2, "_derived_credentials", {})
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse({"success": True, "result": {"id": "tok-id", "status": "active"}})

    assert derive_r2_credentials_from_api_token("secret-token", opener=opener) == (
        "tok-id",
        hashlib.sha256(b"secret-token").hexdigest(),
    )
    # Second call is served from the per-process cache.
    assert derive_r2_credentials_from_api_token("secret-token", opener=opener)
    assert len(calls) == 1


def test_derive_r2_credentials_rejects_inactive_or_blank_tokens(monkeypatch) -> None:
    monkeypatch.setattr(papers_r2, "_derived_credentials", {})

    def inactive(request, timeout):
        return _FakeResponse({"success": True, "result": {"id": "x", "status": "disabled"}})

    def boom(request, timeout):
        raise OSError("network down")

    assert derive_r2_credentials_from_api_token("", opener=inactive) is None
    assert derive_r2_credentials_from_api_token("t", opener=inactive) is None
    assert derive_r2_credentials_from_api_token("t", opener=boom) is None


class FakeR2:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        self.objects[key] = body

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)

    def head_bucket(self) -> bool:
        return True

    def list_objects(self):
        return {
            key: {"bytes": len(body), "etag": hashlib.md5(body).hexdigest()}
            for key, body in self.objects.items()
        }


def _seed_papers(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    papers = tmp_path / "papers"
    (dist / "research" / "articles" / "slug").mkdir(parents=True)
    (dist / "research" / "cards").mkdir(parents=True)
    (dist / "research" / "articles" / "slug" / "index.html").write_text(
        "<html>article</html>", encoding="utf-8"
    )
    (dist / "research" / "cards" / "slug.png").write_bytes(b"png-bytes")
    (dist / "keep.html").write_text("main", encoding="utf-8")
    split_papers_bundle(dist, papers_dir=papers, update_redirects=False, update_headers=False)
    return papers


def test_incremental_papers_publish_uploads_then_skips_unchanged(tmp_path: Path) -> None:
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig(
        account_id="acct",
        bucket="globalid-papers",
        access_key_id="key",
        secret_access_key="secret",
        public_base_url="https://papers.example.test",
    )
    client = FakeR2()
    first = publish_papers_site(
        papers, config, client=client, verify_public=False  # type: ignore[arg-type]
    )
    assert first.uploaded >= 2
    assert first.unchanged == 0
    assert "papers-manifest.json" in client.objects

    second = publish_papers_site(
        papers, config, client=client, verify_public=False  # type: ignore[arg-type]
    )
    assert second.uploaded == 0
    assert second.unchanged >= 2
    assert second.removed == 0


def test_papers_publish_removes_orphans_and_verifies_sample(tmp_path: Path) -> None:
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig(
        account_id="acct",
        bucket="globalid-papers",
        access_key_id="key",
        secret_access_key="secret",
        public_base_url="https://papers.example.test",
    )
    client = FakeR2()
    client.objects["research/articles/old/"] = b"<html>old</html>"
    client.objects["papers-manifest.json"] = json.dumps(
        {
            "schema_version": 1,
            "files": [
                {
                    "path": "research/articles/old/index.html",
                    "key": "research/articles/old/",
                    "bytes": 11,
                    "sha256": "abc",
                    "url": "https://papers.example.test/research/articles/old/",
                }
            ],
        }
    ).encode("utf-8")

    seen_urls: list[str] = []

    class PublicResponse:
        status = 200

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def public_opener(request, timeout):
        seen_urls.append(request.full_url)
        return PublicResponse()

    result = publish_papers_site(
        papers,
        config,
        client=client,  # type: ignore[arg-type]
        verify_public=True,
        public_opener=public_opener,
    )
    assert "research/articles/old/" not in client.objects
    assert result.removed == 1
    assert result.sample_ok is True
    assert seen_urls


def test_public_verify_failure_is_terminal(tmp_path: Path) -> None:
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig(
        account_id="acct",
        bucket="globalid-papers",
        access_key_id="key",
        secret_access_key="secret",
        public_base_url="https://papers.example.test",
    )
    client = FakeR2()

    def public_opener(request, timeout):
        raise R2S3Error("boom")

    with pytest.raises(R2S3Error, match="Public papers verification failed"):
        publish_papers_site(
            papers,
            config,
            client=client,  # type: ignore[arg-type]
            verify_public=True,
            public_opener=public_opener,
        )
    assert "papers-manifest.json" not in client.objects


def test_interrupted_upload_resumes_without_published_manifest(tmp_path):
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig("acct", "bucket", "key", "secret")

    class Interrupted(FakeR2):
        fail = True

        def put_object(self, key, body, **kwargs):
            if key.endswith(".png") and self.fail:
                raise R2S3Error("connection reset")
            super().put_object(key, body, **kwargs)

    client = Interrupted()
    with pytest.raises(R2S3Error, match="connection reset"):
        publish_papers_site(papers, config, client=client, verify_public=False, workers=1)
    assert "research/articles/slug/" in client.objects
    assert "papers-manifest.json" not in client.objects
    client.fail = False
    result = publish_papers_site(papers, config, client=client, verify_public=False, workers=2)
    assert result.uploaded == 1
    assert result.unchanged == 1


def test_actual_remote_bytes_override_stale_success_manifest(tmp_path):
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    client = FakeR2()
    publish_papers_site(papers, config, client=client, verify_public=False)
    # A later failed release overwrote an object but never committed a manifest.
    client.objects["research/articles/slug/"] = b"other release"
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert result.uploaded == 1
    assert client.objects["research/articles/slug/"] == b"<html>article</html>"


def test_uploads_run_concurrently_and_complete_before_manifest(tmp_path):
    import threading
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    barrier = threading.Barrier(2, timeout=3)

    class Parallel(FakeR2):
        def put_object(self, key, body, **kwargs):
            if key != "papers-manifest.json":
                barrier.wait()
            else:
                assert len(self.objects) == 2
            super().put_object(key, body, **kwargs)

    result = publish_papers_site(papers, config, client=Parallel(), verify_public=False, workers=2)
    assert result.uploaded == 2


def test_changed_local_bytes_fail_without_manifest_or_deletion(tmp_path):
    papers = _seed_papers(tmp_path)
    (papers / "research/cards/slug.png").write_bytes(b"bad-bytes")
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    with pytest.raises(R2S3Error, match="checksum"):
        publish_papers_site(papers, config, client=client, verify_public=False)
    assert "papers-manifest.json" not in client.objects


@pytest.mark.parametrize("fail_shard", [False, True])
def test_public_indexes_wait_for_all_shards(tmp_path, fail_shard):
    from src.services.data_release.papers_bundle import build_papers_manifest
    papers = _seed_papers(tmp_path)
    shard_key = "site-data/research/shards/new.json"
    index_key = "site-data/research/index.json"
    shard = papers / shard_key
    shard.parent.mkdir(parents=True)
    shard.write_text('{"records": []}')
    (papers / index_key).write_text('{"file": "shards/new.json"}')
    (papers / "papers-manifest.json").write_text(json.dumps(build_papers_manifest(papers)))

    class CheckingR2(FakeR2):
        def put_object(self, key, body, **kwargs):
            if key == shard_key and fail_shard:
                raise R2S3Error("shard failed")
            if key == index_key:
                assert shard_key in self.objects
            super().put_object(key, body, **kwargs)

    client = CheckingR2()
    client.objects[index_key] = b"previous index"
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    if fail_shard:
        with pytest.raises(R2S3Error, match="shard failed"):
            publish_papers_site(papers, config, client=client, verify_public=False)
        assert client.objects[index_key] == b"previous index"
        assert "papers-manifest.json" not in client.objects
    else:
        publish_papers_site(papers, config, client=client, verify_public=False)
        assert client.objects[index_key] != b"previous index"
