import hashlib
import json
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_immutable import ImmutablePapersError, publish_immutable_papers
from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Client
from src.services.data_release.papers_release_preflight import (
    inspect_bucket_lock, prepare_bound_release, require_indefinite_bucket_lock, verify_bound_candidate,
)
from src.services.data_release.pipeline import _built_site_digest


def document(*rules):
    return {"success": True, "result": {"rules": list(rules)}}


def rule(prefix="releases/sha256/", enabled=True, condition=None):
    return {"enabled": enabled, "prefix": prefix, "condition": condition or {"type": "Indefinite"}}


@pytest.mark.parametrize("response", [document(rule()), document(rule("releases/")), document(rule(""))])
def test_indefinite_lock_must_cover_whole_generation_namespace(response):
    require_indefinite_bucket_lock(response)


@pytest.mark.parametrize("response", [
    {}, {"success": False, "result": {"rules": [rule()]}}, document(),
    document(rule("releases/sha256/a")), document(rule(enabled=False)),
    document(rule(condition={"type": "Age", "maxAgeSeconds": 999999})),
    document(rule(condition={"type": "Date", "date": "2099-01-01T00:00:00Z"})),
])
def test_missing_expiring_or_partial_protection_is_rejected(response):
    with pytest.raises(ImmutablePapersError):
        require_indefinite_bucket_lock(response)


def test_lock_inspection_is_read_only_and_rejects_redirect():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=document(rule()))
    inspect_bucket_lock(account_id="a" * 32, bucket="papers-bucket", token="secret",
                        transport=httpx.MockTransport(handle))
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.host == "api.cloudflare.com"
    with pytest.raises(ImmutablePapersError, match="lookup_failed"):
        inspect_bucket_lock(account_id="a" * 32, bucket="papers-bucket", token="secret",
                            transport=httpx.MockTransport(lambda req: httpx.Response(302, headers={"Location": "https://elsewhere.test"})))


def test_plan_freezes_binding_and_refuses_rebinding(tmp_path):
    (tmp_path / "article.html").write_text("article")
    origin = "https://papers.example"
    manifest = build_papers_manifest(tmp_path, papers_origin=origin)
    pages = {"release_id": "r1", "build_digest": "a" * 64, "source_commit": "abc", "deployment_branch": "main"}
    candidate, bound = prepare_bound_release(papers_dir=tmp_path, manifest=manifest, papers_origin=origin, pages_identity=pages)
    assert bound["papers_manifest_sha256"] == hashlib.sha256(candidate.manifest).hexdigest()
    assert bound["papers_generation_sha256"] == candidate.generation
    _, recovered = prepare_bound_release(papers_dir=tmp_path, manifest=manifest, papers_origin=origin, pages_identity=bound)
    assert recovered == bound
    with pytest.raises(ImmutablePapersError, match="existing_binding_mismatch"):
        prepare_bound_release(papers_dir=tmp_path, manifest=manifest, papers_origin=origin,
                              pages_identity={**bound, "papers_manifest_sha256": "b" * 64})


def test_real_r2_adapter_retries_immutable_generation_without_overwrite(tmp_path):
    (tmp_path / "article.html").write_text("article")
    origin = "https://papers.example"
    manifest = build_papers_manifest(tmp_path, papers_origin=origin)
    pages = {"release_id": "r1", "build_digest": "a" * 64, "source_commit": "abc", "deployment_branch": "main"}
    candidate, identity = prepare_bound_release(papers_dir=tmp_path, manifest=manifest, papers_origin=origin, pages_identity=pages)
    objects, writes = {}, []
    class Response:
        def __init__(self, status, body=b""):
            self.status, self.body = status, body
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return self.body
    def opener(request, timeout):
        key = unquote(urlsplit(request.full_url).path.removeprefix("/bucket/"))
        if request.method == "PUT":
            assert request.get_header("If-none-match") == "*"
            assert "if-none-match" in request.get_header("Authorization")
            if key in objects:
                return Response(409)  # Existing object under an R2 bucket lock.
            objects[key] = request.data
            writes.append(key)
            return Response(200)
        assert request.method == "GET"
        return Response(200, objects[key]) if key in objects else Response(404)
    client = R2S3Client(R2PapersConfig(account_id="acct", bucket="bucket", access_key_id="key", secret_access_key="secret"), opener=opener)
    def public_read(url, limit):
        return objects[url.removeprefix(origin + "/")][:limit]
    first = publish_immutable_papers(candidate=candidate, client=client, public_read=public_read)
    second = publish_immutable_papers(candidate=candidate, client=client, public_read=public_read)
    assert first == second
    assert first["papers_manifest_sha256"] == identity["papers_manifest_sha256"]
    assert writes[-1] == candidate.manifest_key
    assert len(writes) == len(candidate.files) + 1


@pytest.mark.parametrize("damage", ["legacy", "binding", "pages", "papers", "split"])
def test_recovery_requires_frozen_pages_and_papers_candidate(tmp_path, damage):
    papers, dist = tmp_path / "papers", tmp_path / "dist"
    papers.mkdir()
    dist.mkdir()
    (papers / "article.html").write_text("article")
    (dist / "index.html").write_text("site")
    origin = "https://papers.example"
    split = build_papers_manifest(papers, papers_origin=origin)
    (papers / "papers-manifest.json").write_text(json.dumps(split))
    pages = {"release_id": "r1", "build_digest": _built_site_digest(dist), "source_commit": "abc", "deployment_branch": "main"}
    candidate, bound = prepare_bound_release(papers_dir=papers, manifest=split, papers_origin=origin, pages_identity=pages)
    (dist / "release.json").write_text(json.dumps(bound))
    assert verify_bound_candidate(papers_dir=papers, papers_origin=origin, dist=dist, identity=bound) == candidate
    if damage == "legacy": bound = pages
    elif damage == "binding": (dist / "release.json").write_text(json.dumps({**bound, "papers_manifest_sha256": "b" * 64}))
    elif damage == "pages": (dist / "index.html").write_text("changed")
    elif damage == "papers": (papers / "article.html").write_text("changed")
    else: (papers / "papers-manifest.json").write_text("{}")
    with pytest.raises(ImmutablePapersError, match="candidate_invalid"):
        verify_bound_candidate(papers_dir=papers, papers_origin=origin, dist=dist, identity=bound)
