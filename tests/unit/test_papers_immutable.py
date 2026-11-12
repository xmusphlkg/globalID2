from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import threading

import pytest

from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_immutable import (
    ImmutablePapersError, bind_pages_release, prepare_immutable_papers, publish_immutable_papers,
)

ORIGIN = "https://papers.example.test"


class AtomicStore:
    def __init__(self):
        self.objects = {}
        self.lock = threading.Lock()
        self.creates = []
        self.fail_at = None
        self.ambiguous_at = None

    def put_object_conditional(self, key, body, *, previous, **_kwargs):
        assert previous is None
        with self.lock:
            if self.fail_at == key:
                raise OSError("interrupted")
            if key in self.objects:
                raise OSError("precondition failed")
            self.objects[key] = body
            self.creates.append(key)
            if self.ambiguous_at == key:
                raise OSError("response lost")

    def get_object(self, key):
        with self.lock:
            return self.objects.get(key)

    def public_read(self, url, limit):
        assert url.startswith(ORIGIN + "/")
        body = self.get_object(url[len(ORIGIN) + 1:])
        if body is None:
            raise OSError("not public")
        return body[:limit]


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "papers"
    for prefix, body in (("", "English"), ("zh/", "中文")):
        target = root / prefix / "research/articles/paper/index.html"
        target.parent.mkdir(parents=True)
        target.write_text(body)
    manifest = build_papers_manifest(root, papers_origin=ORIGIN)
    return prepare_immutable_papers(papers_dir=root, manifest=manifest, papers_origin=ORIGIN)


def test_generation_content_addressed_and_final_manifest_committed_last(candidate):
    store = AtomicStore()
    binding = publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    assert store.creates[-1] == candidate.manifest_key
    assert binding["papers_manifest_url"] == ORIGIN + "/" + candidate.manifest_key
    assert binding["papers_manifest_sha256"] == hashlib.sha256(candidate.manifest).hexdigest()
    assert binding["papers_generation_sha256"] == candidate.generation
    assert candidate.prefix.startswith("releases/sha256/")
    assert all(key.startswith(candidate.prefix) for key in store.objects)
    identity = {"release_id": "r1", "build_digest": "a" * 64, "source_commit": "c1", "deployment_branch": "main"}
    proposed = bind_pages_release(identity, binding)
    assert proposed["papers_manifest_sha256"] == binding["papers_manifest_sha256"]
    assert "papers_manifest_sha256" not in identity


def test_interrupted_upload_recovers_without_mutating_or_publishing_partial_manifest(candidate):
    store = AtomicStore()
    second = candidate.prefix + candidate.files[1].logical_key
    store.fail_at = second
    with pytest.raises(ImmutablePapersError, match="write_conflict"):
        publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    first = candidate.prefix + candidate.files[0].logical_key
    assert first in store.objects and candidate.manifest_key not in store.objects
    store.fail_at = None
    result = publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    assert result["papers_generation_sha256"] == candidate.generation
    assert store.creates.count(first) == 1


def test_ambiguous_success_is_accepted_only_after_exact_readback(candidate):
    store = AtomicStore()
    store.ambiguous_at = candidate.prefix + candidate.files[0].logical_key
    binding = publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    assert binding["papers_generation_sha256"] == candidate.generation


def test_conflicting_content_is_never_overwritten(candidate):
    store = AtomicStore()
    key = candidate.prefix + candidate.files[0].logical_key
    store.objects[key] = b"unexpected"
    with pytest.raises(ImmutablePapersError, match="write_conflict"):
        publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    assert store.objects[key] == b"unexpected"
    assert candidate.manifest_key not in store.objects


def test_concurrent_identical_publishers_converge_without_overwrites(candidate):
    store = AtomicStore()
    def publish():
        return publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert results[0] == results[1]
    assert len(store.creates) == len(set(store.creates)) == len(candidate.files) + 1


def test_public_mismatch_does_not_return_binding(candidate):
    store = AtomicStore()
    def wrong(_url, _limit): return b"wrong"
    with pytest.raises(ImmutablePapersError, match="public_object_mismatch"):
        publish_immutable_papers(candidate=candidate, client=store, public_read=wrong)
    assert candidate.manifest_key not in store.objects


def test_source_changed_after_preparation_fails_before_manifest(candidate):
    store = AtomicStore()
    (candidate.root / candidate.files[0].path).write_text("changed")
    with pytest.raises(ImmutablePapersError, match="source_changed"):
        publish_immutable_papers(candidate=candidate, client=store, public_read=store.public_read)
    assert candidate.manifest_key not in store.objects


def test_legacy_or_incorrect_binding_does_not_gain_new_identity():
    identity = {"release_id": "r1", "build_digest": "a" * 64, "source_commit": "c1", "deployment_branch": "main"}
    with pytest.raises(ImmutablePapersError, match="binding_invalid"):
        bind_pages_release(identity, {"papers_origin": ORIGIN})
    fake = {"papers_origin": ORIGIN, "papers_generation_sha256": "a" * 64,
            "papers_manifest_sha256": "b" * 64, "papers_manifest_url": ORIGIN + "/papers-manifest.json"}
    with pytest.raises(ImmutablePapersError, match="binding_invalid"):
        bind_pages_release(identity, fake)


def test_generation_changes_when_published_bytes_change(candidate):
    (candidate.root / candidate.files[0].path).write_text("new content")
    new_manifest = build_papers_manifest(candidate.root, papers_origin=ORIGIN)
    new = prepare_immutable_papers(papers_dir=candidate.root, manifest=new_manifest, papers_origin=ORIGIN)
    assert new.generation != candidate.generation


def test_forged_candidate_manifest_cannot_produce_pages_binding(candidate):
    store = AtomicStore()
    forged = replace(candidate, manifest=b'{"files":[]}')
    with pytest.raises(ImmutablePapersError, match="candidate_invalid"):
        publish_immutable_papers(candidate=forged, client=store, public_read=store.public_read)
    assert store.objects == {}
