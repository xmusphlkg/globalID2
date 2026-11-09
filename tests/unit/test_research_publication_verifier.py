from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.services.data_release.pipeline import _built_site_digest
from src.services.data_release import research_publication_verifier as verifier
from src.services.data_release.papers_immutable import ImmutablePapersFile, _candidate_identity

SITE = "https://site.example.test"
PAPERS = "https://papers.example.test"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Response:
    def __init__(self, url, body):
        self.status = 200
        self.url = url
        self.body = body
        self.headers = {}

    def __enter__(self): return self
    def __exit__(self, *_): return None
    def geturl(self): return self.url
    def read(self, size): return self.body[:size]


class Published:
    def __init__(self, root: Path):
        self.astro = root / "astro"
        dist = self.astro / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("published release build")
        article = {"article_id": "lit-1", "slug": "paper-1", "indexable": True,
                   "editorial_status": "published", "integrity_status": "current",
                   "content_tier": "quality_gated_bilingual_evidence",
                   "summary": {"en": {"main_findings": "Important finding."},
                               "zh": {"main_findings": "重要研究发现。"}}}
        self.article_path = self.astro / "src/data/research/articles/paper-1.json"
        self.article_path.parent.mkdir(parents=True)
        self.article_path.write_text(json.dumps(article, ensure_ascii=False))
        self.findings = [{"article_id": "lit-1", "slug": "paper-1",
                          "finding_en": "Important finding.", "finding_zh": "重要研究发现。"}]
        self.identity = {"release_id": "release-1", "build_digest": _built_site_digest(dist),
                         "source_commit": "abc", "deployment_branch": "main", "papers_origin": PAPERS}
        self.remote = {}
        self.manifest_calls = 0
        self.change_after_first_manifest = False
        self.change_page_after_first_fetch = False
        self.page_calls = 0
        self.publish()

    def publish(self, zh_marker="lit-1"):
        article_sha = sha(self.article_path.read_bytes())
        files = []
        bodies = {}
        for prefix in ("", "zh/"):
            path = prefix + "research/articles/paper-1/"
            marker = zh_marker if prefix else "lit-1"
            body = (f'<html lang="{prefix or "en"}"><article data-research-article-id="{marker}" '
                    f'data-research-article-sha256="{article_sha}">Evidence</article></html>').encode()
            bodies[path] = body
            files.append(ImmutablePapersFile(path + "index.html", path, len(body), sha(body)))
        generation, manifest = _candidate_identity(PAPERS, tuple(files))
        self.prefix = f"releases/sha256/{generation}/"
        self.manifest_url = PAPERS + "/" + self.prefix + "papers-manifest.json"
        self.identity["papers_generation_sha256"] = generation
        self.identity["papers_manifest_url"] = self.manifest_url
        for path, body in bodies.items(): self.remote[PAPERS + "/" + self.prefix + path] = body
        self.identity["papers_manifest_sha256"] = sha(manifest)
        self.remote[self.manifest_url] = manifest
        self.remote[SITE + "/release.json"] = json.dumps(self.identity).encode()

    def __call__(self, request, *, timeout):
        url = request.full_url
        if url == PAPERS + "/" + self.prefix + "research/articles/paper-1/":
            self.page_calls += 1
            if self.change_page_after_first_fetch and self.page_calls > 1:
                return Response(url, b"in-place page replacement")
        if url == self.manifest_url:
            self.manifest_calls += 1
            if self.change_after_first_manifest and self.manifest_calls > 1:
                return Response(url, b'{"changed":true}')
        return Response(url, self.remote[url])

    def verify(self, findings=None, identity=None):
        return verifier.verify_research_publication(
            astro_dir=self.astro, identity=identity or self.identity, public_base_url=SITE,
            papers_base_url=PAPERS, findings=self.findings if findings is None else findings, opener=self)


@pytest.fixture
def published(tmp_path):
    return Published(tmp_path)


def test_checks_exact_public_binding_bilingual_pages_and_recheck(published):
    proof = published.verify()
    assert proof.release_id == "release-1"
    assert proof.papers_manifest_sha256 == published.identity["papers_manifest_sha256"]
    assert proof.articles[0][0] == "lit-1"
    assert verifier.recheck_research_publication(previous=proof, astro_dir=published.astro,
        identity=published.identity, public_base_url=SITE, papers_base_url=PAPERS,
        findings=published.findings, opener=published) == proof


def test_empty_findings_still_verify_binding(published):
    assert published.verify(findings=[]).articles == ()
    published.remote[published.manifest_url] += b" "
    with pytest.raises(verifier.ResearchPublicationError, match="papers_manifest_digest_mismatch"):
        published.verify(findings=[])


def test_legacy_release_missing_binding_is_rejected_before_network(published):
    identity = {key: value for key, value in published.identity.items() if key != "papers_manifest_sha256"}
    with pytest.raises(verifier.ResearchPublicationError, match="papers_release_binding_required"):
        published.verify(identity=identity)
    assert published.manifest_calls == 0


def test_public_page_change_and_stale_brief_fail_closed(published):
    published.remote[PAPERS + "/" + published.prefix + "zh/research/articles/paper-1/"] += b"modified"
    with pytest.raises(verifier.ResearchPublicationError, match="research_page_digest_mismatch"):
        published.verify()
    published.publish()
    findings = [{**published.findings[0], "finding_zh": "旧版结果。"}]
    with pytest.raises(verifier.ResearchPublicationError, match="research_summary_mismatch"):
        published.verify(findings=findings)


@pytest.mark.parametrize("change", ["retracted", "expression_of_concern", "corrected", "withdrawn"])
def test_retracted_corrected_or_withdrawn_article_is_never_accepted(published, change):
    article = json.loads(published.article_path.read_text())
    if change == "withdrawn":
        article["editorial_status"] = "review"
    else:
        article["integrity_status"] = change
    published.article_path.write_text(json.dumps(article, ensure_ascii=False))
    published.publish()
    with pytest.raises(verifier.ResearchPublicationError, match="research_article_not_current"):
        published.verify()


def test_unbound_or_duplicated_page_marker_fails_despite_matching_manifest(published):
    published.publish(zh_marker="lit-2")
    with pytest.raises(verifier.ResearchPublicationError, match="article_page_version_mismatch"):
        published.verify()


@pytest.mark.parametrize("change", ["extra_file", "reordered", "escaped_key", "forged_generation"])
def test_manifest_must_recompute_entire_generation_and_stay_in_prefix(published, change):
    manifest = json.loads(published.remote[published.manifest_url])
    if change == "extra_file":
        path = "research/articles/other/index.html"
        key = published.prefix + "research/articles/other/"
        manifest["files"].append({"path": path, "key": key, "url": PAPERS + "/" + key,
                                  "bytes": 1, "sha256": sha(b"x")})
        manifest["file_count"] += 1
    elif change == "reordered":
        manifest["files"].reverse()
    elif change == "escaped_key":
        manifest["files"][0]["key"] = "releases/sha256/" + "f" * 64 + "/escape"
        manifest["files"][0]["url"] = PAPERS + "/" + manifest["files"][0]["key"]
    else:
        old_prefix = published.prefix
        generation = "f" * 64
        published.prefix = f"releases/sha256/{generation}/"
        published.manifest_url = PAPERS + "/" + published.prefix + "papers-manifest.json"
        published.identity["papers_generation_sha256"] = generation
        published.identity["papers_manifest_url"] = published.manifest_url
        manifest["generation_sha256"] = generation
        for item in manifest["files"]:
            item["key"] = published.prefix + item["key"][len(old_prefix):]
            item["url"] = PAPERS + "/" + item["key"]
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    published.identity["papers_manifest_sha256"] = sha(raw)
    published.remote[published.manifest_url] = raw
    published.remote[SITE + "/release.json"] = json.dumps(published.identity).encode()
    with pytest.raises(verifier.ResearchPublicationError, match="papers_manifest_generation_invalid"):
        published.verify(findings=[])


def test_manifest_changes_during_verification_fail(published):
    published.change_after_first_manifest = True
    with pytest.raises(verifier.ResearchPublicationError, match="papers_manifest_digest_mismatch"):
        published.verify()


def test_page_changes_before_manifest_commit_fail(published):
    published.change_page_after_first_fetch = True
    with pytest.raises(verifier.ResearchPublicationError, match="research_page_digest_mismatch"):
        published.verify()


def test_changed_public_identity_is_not_valid_at_send_time(published):
    proof = published.verify()
    live = json.loads(published.remote[SITE + "/release.json"])
    live["release_id"] = "new-release"
    published.remote[SITE + "/release.json"] = json.dumps(live).encode()
    with pytest.raises(verifier.ResearchPublicationError, match="pages_release_identity_mismatch"):
        verifier.recheck_research_publication(previous=proof, astro_dir=published.astro,
            identity=published.identity, public_base_url=SITE, papers_base_url=PAPERS,
            findings=published.findings, opener=published)
