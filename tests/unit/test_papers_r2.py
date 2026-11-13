from __future__ import annotations

import hashlib
import gzip
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


def test_template_v2_release_is_rejected_before_upload(tmp_path, monkeypatch):
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    (candidate / 'current.json').write_bytes(b'new')
    class Client:
        pointer = b'old'
        def get_object(self, key): return self.pointer
        def put_object_conditional(self, *args, **kwargs): pytest.fail('pointer switched')
    monkeypatch.setattr(papers_r2, '_publish_papers_site', lambda *a, **kw: pytest.fail('assets published'))
    config = R2PapersConfig(account_id='a', bucket='b', access_key_id='c', secret_access_key='d')
    with pytest.raises(R2S3Error, match='template v2 publication has been removed'):
        publish_papers_site(tmp_path, config, client=Client(), template_candidate=candidate, lock_dir=tmp_path / 'locks')


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
        self.encodings: dict[str, str | None] = {}

    def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None = None,
        cache_control: str | None = None,
        content_encoding: str | None = None,
    ) -> None:
        self.objects[key] = body
        self.encodings[key] = content_encoding

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


def test_publisher_rejects_obsolete_bundle_before_upload(tmp_path):
    papers = _seed_papers(tmp_path)
    manifest_path = papers / "papers-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"].append({"path": "site-data/research/shards/old.json", "key": "site-data/research/shards/old.json"})
    manifest_path.write_text(json.dumps(manifest))
    client = FakeR2()
    with pytest.raises(R2S3Error, match="Obsolete research files"):
        publish_papers_site(papers, R2PapersConfig("acct", "bucket", "key", "secret"), client=client, verify_public=False)
    assert not client.objects


def test_storage_budget_accounts_for_other_channels_before_any_write(tmp_path):
    papers = _seed_papers(tmp_path)
    client = FakeR2()
    client.objects['site-data/surveillance/current.json'] = b'x' * 1000
    before = dict(client.objects)
    with pytest.raises(R2S3Error, match='storage budget exceeded before upload'):
        publish_papers_site(papers, R2PapersConfig('acct', 'bucket', 'key', 'secret'),
                            client=client, verify_public=False, max_total_bytes=100)
    assert client.objects == before


def test_publisher_concurrency_is_bounded_by_the_connection_pool(tmp_path):
    config = R2PapersConfig('acct', 'bucket', 'key', 'secret')
    for workers in (0, 97):
        with pytest.raises(ValueError, match='between 1 and 96'):
            publish_papers_site(tmp_path, config, client=FakeR2(), workers=workers)


def test_static_html_is_stored_gzipped_and_reused(tmp_path):
    papers = _seed_papers(tmp_path)
    page = papers / 'research/articles/slug/index.html'
    original = ('<html>' + 'paper evidence ' * 500 + '</html>').encode()
    page.write_bytes(original)
    (papers / 'papers-manifest.json').unlink()
    client = FakeR2()
    config = R2PapersConfig('acct', 'bucket', 'key', 'secret')
    publish_papers_site(papers, config, client=client, verify_public=False)
    key = 'research/articles/slug/'
    assert client.encodings[key] == 'gzip'
    assert gzip.decompress(client.objects[key]) == original
    assert len(client.objects[key]) < len(original)
    again = publish_papers_site(papers, config, client=client, verify_public=False)
    assert again.uploaded == 0


def test_existing_uncompressed_content_is_reused_only_if_remote_bytes_match(tmp_path):
    papers = _seed_papers(tmp_path)
    page = papers / 'research/articles/slug/index.html'
    original = ('<html>' + 'published evidence ' * 300 + '</html>').encode()
    page.write_bytes(original)
    (papers / 'papers-manifest.json').unlink()
    client = FakeR2()
    config = R2PapersConfig('acct', 'bucket', 'key', 'secret')
    publish_papers_site(papers, config, client=client, verify_public=False)
    key = 'research/articles/slug/'
    # Simulate a prior release stored without gzip, with the same logical
    # source digest in its published manifest.
    client.objects[key] = original
    client.encodings[key] = None
    again = publish_papers_site(papers, config, client=client, verify_public=False)
    assert again.uploaded == 0
    assert client.objects[key] == original
    assert client.encodings[key] is None
    # A stale manifest does not authorize reuse of a modified object.
    client.objects[key] = b'x' * len(original)
    repaired = publish_papers_site(papers, config, client=client, verify_public=False)
    assert repaired.uploaded == 1
    assert gzip.decompress(client.objects[key]) == original


@pytest.mark.parametrize("already_uploaded", [False, True])
def test_disabled_cards_remove_previous_remote_cards(tmp_path, monkeypatch, already_uploaded):
    papers = _seed_papers(tmp_path)
    config = R2PapersConfig(account_id="acct", bucket="papers", access_key_id="key", secret_access_key="secret")
    client = FakeR2()
    if already_uploaded:
        publish_papers_site(papers, config, client=client, verify_public=False)
    monkeypatch.setattr(papers_r2, "research_social_cards_enabled", lambda: False)
    # Even missing/corrupt old local card files are not read for publication.
    (papers / "research/cards/slug.png").unlink()
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert result.removed == int(already_uploaded)
    assert client.objects.get("research/cards/slug.png") is None
    manifest = json.loads(client.objects["papers-manifest.json"])
    assert manifest["article_social_cards_enabled"] is False
    assert not any(item["key"] == "research/cards/slug.png" for item in manifest["files"])
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
        assert request.get_header("User-agent") == "GIDS-Release-Verifier/1.0"
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
    shard_key = "site-data/reports/shards/new.json"
    index_key = "site-data/reports/index.json"
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


@pytest.mark.parametrize("content", ["small", "gzip", "incompressible"])
def test_warm_publish_never_opens_unchanged_artifact_bodies(tmp_path, monkeypatch, content):
    papers = _seed_papers(tmp_path)
    if content != "small":
        import random
        body = (random.Random(42).randbytes(2048) if content == "incompressible"
                else b"<html>" + b"evidence " * 1000 + b"</html>")
        (papers / "research/articles/slug/index.html").write_bytes(body)
        (papers / "papers-manifest.json").unlink()
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
    monkeypatch.setattr(papers_r2.gzip, "compress", lambda *a, **kw: pytest.fail("Unchanged artifact was compressed again"))
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert (result.uploaded, result.unchanged, result.bytes_uploaded) == (0, 2, 0)
    assert reads == []


def test_cached_gzip_receipt_still_repairs_changed_remote_object(tmp_path, monkeypatch):
    import os
    papers = _seed_papers(tmp_path)
    page = papers / "research/articles/slug/index.html"
    page.write_bytes(b"<html>" + b"evidence " * 1000 + b"</html>")
    (papers / "papers-manifest.json").unlink()
    time.sleep(1.01)
    client = FakeR2()
    config = R2PapersConfig("acct", "bucket", "key", "secret")
    publish_papers_site(papers, config, client=client, verify_public=False)
    key = "research/articles/slug/"
    original = client.objects[key]
    client.objects[key] = b"x" * len(original)  # same size, different remote ETag
    reads = []
    original_read = Path.read_bytes

    def record_read(path):
        if path.is_relative_to(papers):
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    result = publish_papers_site(papers, config, client=client, verify_public=False)
    assert (result.uploaded, result.unchanged) == (1, 1)
    assert reads == [page]
    assert client.objects[key] == original
    before = page.stat()
    page.write_bytes(b"x" * before.st_size)
    os.utime(page, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(R2S3Error, match="checksum"):
        publish_papers_site(papers, config, client=client, verify_public=False)
    assert client.objects[key] == original


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


def test_template_mode_keeps_old_static_article_and_assets_without_reupload(tmp_path):
    papers = _seed_papers(tmp_path)
    assets = papers / '_astro'; assets.mkdir()
    (assets / 'old.css').write_text('body{color:red}')
    (papers / 'papers-manifest.json').unlink()
    client = FakeR2(); config = R2PapersConfig('acct', 'bucket', 'key', 'secret')
    publish_papers_site(papers, config, client=client, verify_public=False)
    key = 'research/articles/slug/'
    old = client.objects[key]
    previous = json.loads(client.objects['papers-manifest.json'])
    previous_article = next(x for x in previous['files'] if x['key'] == key)
    client.objects['paper-template-v2/current.json'] = b'{"schema":1}'
    (papers / 'research/articles/slug/index.html').write_text('<html>new shared styles</html>')
    (assets / 'old.css').unlink()
    (assets / 'new.css').write_text('body{color:blue}')
    (papers / 'papers-manifest.json').unlink()
    result = publish_papers_site(papers, config, client=client, verify_public=False, preserve_static_articles=True)
    assert result.uploaded == 1
    assert client.objects[key] == old
    assert '_astro/old.css' in client.objects and '_astro/new.css' in client.objects
    manifest = json.loads(client.objects['papers-manifest.json'])
    assert next(x for x in manifest['files'] if x['key'] == key)['sha256'] == previous_article['sha256']
    assert '_astro/old.css' in manifest['template_legacy_asset_keys']


def test_template_mode_requires_active_pointer_before_mutation(tmp_path):
    papers = _seed_papers(tmp_path); client = FakeR2()
    config = R2PapersConfig('acct', 'bucket', 'key', 'secret')
    with pytest.raises(R2S3Error, match='not active'):
        publish_papers_site(papers, config, client=client, verify_public=False, preserve_static_articles=True)
    assert not client.objects
