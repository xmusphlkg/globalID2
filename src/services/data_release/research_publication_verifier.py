"""Read-only Pages/Papers checks; deliberately not wired to sending.

Current R2 pages are mutable and Pages does not bind the final Papers manifest.
A recheck detects changes but cannot make publication atomic with SMTP delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.automation.dispatch_research_digest import _https_origin
from .papers_immutable import ImmutablePapersError, ImmutablePapersFile, _candidate_identity
from .research_subscription import _verify_built_artifact

MAX_RELEASE = 65536
MAX_MANIFEST = 32 * 1024 * 1024
MAX_PAGE = 5 * 1024 * 1024
MAX_ARTICLE = 2 * 1024 * 1024
HEX = re.compile(r"^[a-f0-9]{64}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
IDENTITY_KEYS = ("release_id", "build_digest", "source_commit", "deployment_branch",
                 "papers_origin", "papers_manifest_sha256", "papers_generation_sha256", "papers_manifest_url")


class ResearchPublicationError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class VerifiedResearchPublication:
    release_id: str
    pages_build_digest: str
    papers_origin: str
    papers_manifest_sha256: str
    papers_generation_sha256: str
    papers_manifest_url: str
    # Each tuple is article ID, article JSON SHA, en page SHA, zh page SHA.
    articles: tuple[tuple[str, str, str, str], ...]


class _ArticleMarkers(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.markers: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = [key for key, _ in attrs]
        if "data-research-article-id" not in names and "data-research-article-sha256" not in names:
            return
        if tag != "article" or names.count("data-research-article-id") != 1 or names.count("data-research-article-sha256") != 1:
            raise ResearchPublicationError("invalid_article_page_marker")
        values = dict(attrs)
        self.markers.append((values.get("data-research-article-id") or "",
                             values.get("data-research-article-sha256") or ""))


def _fetch(url: str, limit: int, opener: Callable[..., Any]) -> bytes:
    request = Request(url, headers={"Cache-Control": "no-cache", "Accept-Encoding": "identity",
                                    "User-Agent": "GIDS-Release-Verifier/1.0"})
    try:
        with opener(request, timeout=20) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            if status != 200 or response.geturl() != url:
                raise ValueError("unexpected_status_or_redirect")
            encoding = (response.headers.get("Content-Encoding") or "").lower().strip()
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("response_too_large")
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(limit + 1)
            elif encoding not in ("", "identity"):
                raise ValueError("unsupported_content_encoding")
            if len(raw) > limit:
                raise ValueError("response_too_large")
            return raw
    except Exception as exc:
        raise ResearchPublicationError("research_public_fetch_failed") from exc


def _json(raw: bytes, error: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("object_required")
        return result
    except (ValueError, UnicodeDecodeError) as exc:
        raise ResearchPublicationError(error) from exc


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _page_markers(html: bytes, article_id: str, article_sha: str) -> None:
    try:
        parser = _ArticleMarkers()
        parser.feed(html.decode("utf-8"))
        parser.close()
    except (UnicodeDecodeError, ValueError) as exc:
        raise ResearchPublicationError("article_page_invalid") from exc
    if parser.markers != [(article_id, article_sha)]:
        raise ResearchPublicationError("article_page_version_mismatch")


def verify_research_publication(*, astro_dir: Path, identity: Mapping[str, Any],
                                public_base_url: str, papers_base_url: str,
                                findings: Sequence[Mapping[str, Any]],
                                opener: Callable[..., Any] | None = None) -> VerifiedResearchPublication:
    """Verify release identity, manifest, article data and both public pages.

    Identity must come from the frozen candidate, not the live response.
    Findings carry article_id, slug, finding_en and finding_zh. Legacy releases
    lacking the Papers binding fail closed. This function never sends mail.
    """
    if len(findings) > 100:
        raise ResearchPublicationError("research_findings_invalid")
    try:
        public_origin = _https_origin(public_base_url, "public_base_url")
        papers_origin = _https_origin(papers_base_url, "papers_base_url")
    except Exception as exc:
        raise ResearchPublicationError("research_origin_invalid") from exc
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in IDENTITY_KEYS):
        raise ResearchPublicationError("papers_release_binding_required")
    generation = identity["papers_generation_sha256"]
    if (identity["papers_origin"] != papers_origin or not HEX.fullmatch(identity["papers_manifest_sha256"])
            or not HEX.fullmatch(identity["build_digest"]) or not HEX.fullmatch(generation)
            or identity["papers_manifest_url"] != f"{papers_origin}/releases/sha256/{generation}/papers-manifest.json"):
        raise ResearchPublicationError("papers_release_binding_invalid")
    try:
        _verify_built_artifact(astro_dir, identity)
    except Exception as exc:
        raise ResearchPublicationError("pages_build_verification_failed") from exc
    open_url = opener or build_opener(_NoRedirect()).open
    release_url = public_origin + "/release.json"
    manifest_url = identity["papers_manifest_url"]
    generation_prefix = f"releases/sha256/{generation}/"

    def check_release() -> bytes:
        raw = _fetch(release_url, MAX_RELEASE, open_url)
        live = _json(raw, "pages_release_invalid")
        if any(live.get(key) != identity[key] for key in IDENTITY_KEYS):
            raise ResearchPublicationError("pages_release_identity_mismatch")
        return raw

    def check_manifest() -> tuple[bytes, dict[str, Any]]:
        raw = _fetch(manifest_url, MAX_MANIFEST, open_url)
        if _digest(raw) != identity["papers_manifest_sha256"]:
            raise ResearchPublicationError("papers_manifest_digest_mismatch")
        manifest = _json(raw, "papers_manifest_invalid")
        if (manifest.get("schema_version") != 1 or manifest.get("papers_origin") != papers_origin
                or manifest.get("generation_sha256") != generation
                or not isinstance(manifest.get("files"), list) or not 1 <= len(manifest["files"]) <= 100000):
            raise ResearchPublicationError("papers_manifest_invalid")
        try:
            files = []
            for item in manifest["files"]:
                if not isinstance(item, dict):
                    raise ValueError("invalid item")
                path, key, size, digest = item.get("path"), item.get("key"), item.get("bytes"), item.get("sha256")
                if (not isinstance(path, str) or not path or path.startswith("/") or
                        any(part in {"", ".", ".."} for part in path.split("/")) or
                        not isinstance(key, str) or not key.startswith(generation_prefix) or
                        item.get("url") != papers_origin + "/" + key or type(size) is not int or
                        not isinstance(digest, str)):
                    raise ValueError("invalid entry")
                files.append(ImmutablePapersFile(path, key[len(generation_prefix):], size, digest))
            computed_generation, canonical = _candidate_identity(papers_origin, tuple(files))
            if computed_generation != generation or canonical != raw:
                raise ValueError("generation or canonical manifest mismatch")
        except (ImmutablePapersError, ValueError, TypeError, AttributeError) as exc:
            raise ResearchPublicationError("papers_manifest_generation_invalid") from exc
        return raw, manifest

    release_raw = check_release()
    manifest_raw, manifest = check_manifest()
    entries: dict[str, dict[str, Any]] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ResearchPublicationError("papers_manifest_invalid")
        path = item["path"]
        if path in entries or path.startswith("/") or ".." in path.split("/"):
            raise ResearchPublicationError("papers_manifest_invalid")
        entries[path] = item

    verified: list[tuple[str, str, str, str]] = []
    verified_pages: list[tuple[str, int, str, str, str]] = []
    seen_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ResearchPublicationError("research_findings_invalid")
        article_id, slug = finding.get("article_id"), finding.get("slug")
        if (not isinstance(article_id, str) or not article_id or len(article_id) > 160 or article_id in seen_ids
                or not isinstance(slug, str) or not SLUG.fullmatch(slug)):
            raise ResearchPublicationError("research_findings_invalid")
        seen_ids.add(article_id)
        article_path = astro_dir / "src" / "data" / "research" / "articles" / f"{slug}.json"
        try:
            if article_path.is_symlink() or not article_path.is_file() or article_path.stat().st_size > MAX_ARTICLE:
                raise ValueError("article_invalid")
            article_raw = article_path.read_bytes()
            article = _json(article_raw, "research_article_invalid")
        except (OSError, ValueError) as exc:
            raise ResearchPublicationError("research_article_unavailable") from exc
        if (article.get("article_id") != article_id or article.get("slug") != slug
                or article.get("editorial_status") != "published" or article.get("indexable") is not True
                or article.get("integrity_status") != "current"
                or article.get("content_tier") not in {"quality_gated_bilingual_evidence", "quality_gated_multilingual_evidence"}):
            raise ResearchPublicationError("research_article_not_current")
        summary = article.get("summary")
        if not isinstance(summary, dict):
            raise ResearchPublicationError("research_summary_mismatch")
        for locale in ("en", "zh"):
            item = summary.get(locale)
            value = finding.get("finding_" + locale)
            if (not isinstance(item, dict) or not isinstance(value, str) or not value.strip()
                    or not isinstance(item.get("main_findings"), str)
                    or item["main_findings"].strip() != value.strip()):
                raise ResearchPublicationError("research_summary_mismatch")
        article_sha = _digest(article_raw)
        page_shas = []
        for prefix in ("", "zh/"):
            path = prefix + "research/articles/" + slug + "/index.html"
            entry = entries.get(path)
            expected_key = generation_prefix + prefix + "research/articles/" + slug + "/"
            expected_url = papers_origin + "/" + expected_key
            if (not entry or entry.get("key") != expected_key or entry.get("url") != expected_url
                    or not isinstance(entry.get("sha256"), str) or not HEX.fullmatch(entry["sha256"])
                    or type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= MAX_PAGE):
                raise ResearchPublicationError("research_article_not_in_manifest")
            page = _fetch(expected_url, MAX_PAGE, open_url)
            if len(page) != entry["bytes"] or _digest(page) != entry["sha256"]:
                raise ResearchPublicationError("research_page_digest_mismatch")
            _page_markers(page, article_id, article_sha)
            page_shas.append(entry["sha256"])
            verified_pages.append((expected_url, entry["bytes"], entry["sha256"], article_id, article_sha))
        verified.append((article_id, article_sha, page_shas[0], page_shas[1]))

    if check_release() != release_raw or check_manifest()[0] != manifest_raw:
        raise ResearchPublicationError("research_publication_changed")
    # The publisher updates stable R2 paths in place, including before its
    # manifest changes. A second read catches a page changing mid-check even
    # when the old completion manifest is still publicly visible.
    for url, expected_size, page_sha, article_id, article_sha in verified_pages:
        page = _fetch(url, MAX_PAGE, open_url)
        if len(page) != expected_size or _digest(page) != page_sha:
            raise ResearchPublicationError("research_page_digest_mismatch")
        _page_markers(page, article_id, article_sha)
    if check_release() != release_raw or check_manifest()[0] != manifest_raw:
        raise ResearchPublicationError("research_publication_changed")
    try:
        _verify_built_artifact(astro_dir, identity)
    except Exception as exc:
        raise ResearchPublicationError("pages_build_verification_failed") from exc
    return VerifiedResearchPublication(identity["release_id"], identity["build_digest"], papers_origin,
                                       identity["papers_manifest_sha256"], generation, manifest_url, tuple(sorted(verified)))


def recheck_research_publication(*, previous: VerifiedResearchPublication, **kwargs: Any) -> VerifiedResearchPublication:
    """Repeat all public and content checks immediately before a future send."""
    current = verify_research_publication(**kwargs)
    if current != previous:
        raise ResearchPublicationError("research_publication_changed")
    return current
