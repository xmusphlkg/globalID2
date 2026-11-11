from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Self

import pytest

from src.services.data_release import papers_bundle, papers_r2
from src.services.data_release.papers_bundle import split_papers_bundle
from src.services.data_release.papers_r2 import (
    R2PapersConfig,
    R2S3Error,
    derive_r2_credentials_from_api_token,
    publish_papers_site,
)


@pytest.fixture(autouse=True)
def enable_cards_for_publisher_scenarios(monkeypatch):
    # Existing publisher integrity scenarios include PNGs. Disabled-mode
    # coverage below explicitly overrides the production-default-off switch.
    monkeypatch.setattr(papers_bundle, "research_social_cards_enabled", lambda: True)
    monkeypatch.setattr(papers_r2, "research_social_cards_enabled", lambda: True)


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


@pytest.mark.parametrize("already_uploaded", [False, True])
def test_disabled_cards_skip_legacy_upload_and_preserve_existing_remote_cards(tmp_path, monkeypatch, already_uploaded):
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig(account_id="acct", bucket="papers", access_key_id="key", secret_access_key="secret")
    client = FakeR2()
    if already_uploaded:
        publish_papers_site(papers, config, client=client, verify_public=False)
    monkeypatch.setattr(papers_r2, "research_social_cards_enabled", lambda: False)
    # Even missing/corrupt old local card files are not read for publication.
    (papers / "research/cards/slug.png").unlink()
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert result.removed == 0
    assert client.objects.get("research/cards/slug.png") == (b"png-bytes" if already_uploaded else None)
    manifest = json.loads(client.objects["papers-manifest.json"])
    assert manifest["article_social_cards_enabled"] is False
    assert any(item["key"] == "research/cards/slug.png" for item in manifest["files"]) is already_uploaded
    assert manifest["file_count"] == len(manifest["files"])
    repeat = publish_papers_site(papers, config, client=client, verify_public=False)
    assert repeat.uploaded == 0
    assert repeat.removed == 0


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


@pytest.mark.parametrize("fail_shard", [False, True])
def test_disease_library_shards_publish_before_index_and_html(tmp_path, fail_shard):
    from src.services.data_release.papers_bundle import build_papers_manifest
    papers = _seed_papers(tmp_path)
    shard_key = "research/catalogue/diseases/d038/0-" + "a" * 16 + ".json"
    index_key = "research/catalogue/diseases/d038.json"
    html_key = "zh/research/diseases/influenza/"
    shard = papers / shard_key
    shard.parent.mkdir(parents=True)
    shard.write_text('{"articles": []}')
    (papers / index_key).write_text(json.dumps({"shard": shard_key}))
    html = papers / html_key / "index.html"
    html.parent.mkdir(parents=True)
    html.write_text('<main>New disease library</main>')
    (papers / "papers-manifest.json").write_text(json.dumps(build_papers_manifest(papers)))
    calls = []

    class CheckingR2(FakeR2):
        def put_object(self, key, body, **kwargs):
            calls.append(key)
            if key == shard_key and fail_shard:
                raise R2S3Error("library shard failed")
            if key == index_key:
                assert shard_key in self.objects
            if key == html_key:
                assert self.objects[index_key] != b"previous index"
            super().put_object(key, body, **kwargs)

    client = CheckingR2()
    client.objects[index_key] = b"previous index"
    client.objects[html_key] = b"previous library"
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    if fail_shard:
        with pytest.raises(R2S3Error, match="library shard failed"):
            publish_papers_site(papers, config, client=client, verify_public=False, workers=4)
        assert client.objects[index_key] == b"previous index"
        assert client.objects[html_key] == b"previous library"
        assert index_key not in calls
        assert html_key not in calls
    else:
        publish_papers_site(papers, config, client=client, verify_public=False, workers=4)
        assert calls.index(shard_key) < calls.index(index_key) < calls.index(html_key)


def test_only_content_named_library_pages_receive_immutable_cache_metadata():
    shard = "research/catalogue/diseases/d038/0-" + "a" * 16 + ".json"
    assert papers_r2._cache_control_for(shard) == "public, max-age=31536000, immutable"
    study = "research/catalogue/studies/mathematical-modelling-" + "a" * 16 + ".json"
    assert papers_r2._cache_control_for(study) == "public, max-age=31536000, immutable"
    for mutable in ["research/catalogue/diseases/d038.json", "research/catalogue/diseases/d038/page-0.json", "site-data/research/index.json", "research/catalogue/studies/mathematical-modelling.json", "research/catalogue/studies/review-bad-hash.json", "research/catalogue/manifest.json"]:
        assert "immutable" not in papers_r2._cache_control_for(mutable)


def test_warm_publish_never_opens_unchanged_artifact_bodies(tmp_path, monkeypatch):
    papers = _seed_papers(tmp_path)
    time.sleep(1.01)  # stable timestamps before warming publication receipts
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    publish_papers_site(papers, config, client=client, verify_public=False)
    reads = []
    original_read = Path.read_bytes

    def record_read(path):
        if path.is_relative_to(papers):
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert (result.uploaded, result.unchanged, result.bytes_uploaded) == (0, 2, 0)
    assert reads == []


def test_one_changed_file_only_reads_and_uploads_that_file(tmp_path, monkeypatch):
    from src.services.data_release.papers_bundle import build_papers_manifest
    papers = _seed_papers(tmp_path)
    time.sleep(1.01)  # stable timestamps before warming publication receipts
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    publish_papers_site(papers, config, client=client, verify_public=False)
    changed = papers / "research/cards/slug.png"
    changed.write_bytes(b"new-card")
    (papers / "papers-manifest.json").write_text(json.dumps(build_papers_manifest(papers)))
    reads = []
    original_read = Path.read_bytes

    def record_read(path):
        if path.is_relative_to(papers):
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert (result.uploaded, result.unchanged) == (1, 1)
    assert reads == [changed]
    assert client.objects["research/cards/slug.png"] == b"new-card"


def test_missing_local_receipt_recovers_once_and_then_skips_reads(tmp_path, monkeypatch):
    from src.services.data_release.artifact_index import ArtifactIndex
    papers = _seed_papers(tmp_path)
    time.sleep(1.01)  # stable timestamps before warming publication receipts
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    publish_papers_site(papers, config, client=client, verify_public=False)
    ArtifactIndex(papers).path.unlink()
    reads = []
    original_read = Path.read_bytes

    def record_read(path):
        if path.is_relative_to(papers):
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    assert publish_papers_site(papers, config, client=client, verify_public=False).uploaded == 0
    assert len(reads) == 2
    reads.clear()
    assert publish_papers_site(papers, config, client=client, verify_public=False).uploaded == 0
    assert reads == []


def test_same_size_tampering_cannot_be_skipped_by_remote_match(tmp_path):
    import os
    papers = _seed_papers(tmp_path)
    time.sleep(1.01)  # stable timestamps before warming publication receipts
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    publish_papers_site(papers, config, client=client, verify_public=False)
    path = papers / "research/cards/slug.png"
    before = path.stat()
    path.write_bytes(b"bad-bytes")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(R2S3Error, match="checksum"):
        publish_papers_site(papers, config, client=client, verify_public=False)
    assert client.objects["research/cards/slug.png"] == b"png-bytes"
