"""Split Astro dist into Pages main + papers (R2) bundles.

Long-tail research article pages, card PNGs, and ``site-data/research`` shards
move to the papers host so Cloudflare Pages stays under its file-count ceiling.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .artifact_index import ArtifactIndex
from .artifact_paths import artifact_files, relative_key
from .build_fingerprints import BuildFingerprints
from .bundle_assets import copy_bundle_assets
from .bundle_html_workers import rewrite_changed_pages
from .research_publishing import is_research_card, research_social_cards_enabled

DEFAULT_MAIN_SITE_ORIGIN = "https://globalinfectiousdisease.com"
DEFAULT_PAPERS_ORIGIN = "https://papers.globalinfectiousdisease.com"
DEFAULT_PAGES_FILE_BUDGET = 20_000
DEFAULT_PAGES_FILE_WARN = 18_000

# Directory in dist holding files that live at the root of the papers host.
PAPERS_HOST_DIR = "papers-host"

LIBRARY_SHARD_RETENTION_SECONDS = 48 * 60 * 60
LIBRARY_SHARD_PATH = re.compile(
    r"(?:research/catalogue/diseases/[a-z0-9_-]+/[0-9]+-[a-f0-9]{16,64}"
    r"|research/catalogue/studies/[a-z0-9_-]+-[a-f0-9]{16,64})\.json"
)


def retain_library_shards(
    papers_dir: Path, expected: set[Path], previous_manifest: dict,
    index: ArtifactIndex, *, now: float | None = None,
) -> dict[str, float]:
    """Keep previously referenced immutable pages for cached catalogue indexes.

    Only exact shard paths recorded by the preceding bundle are eligible.
    Reusing an active shard clears its retirement clock; repeated splits never
    extend the clock of a shard that remains retired.
    """
    instant = time.time() if now is None else now
    retirements = previous_manifest.get("library_shard_retirements")
    retirements = retirements if isinstance(retirements, dict) else {}
    retained: dict[str, float] = {}
    root = papers_dir.resolve()
    for entry in previous_manifest.get("files", []):
        if not isinstance(entry, dict):
            continue
        relative = entry.get("path")
        if not isinstance(relative, str) or not LIBRARY_SHARD_PATH.fullmatch(relative):
            continue
        path = papers_dir / relative
        if path in expected:
            continue
        retired = retirements.get(relative, instant)
        if type(retired) not in (int, float) or not math.isfinite(retired) or retired > instant:
            retired = instant
        if instant - retired >= LIBRARY_SHARD_RETENTION_SECONDS:
            continue
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Invalid retained literature shard: {relative}")
        if not path.is_file():
            raise FileNotFoundError(f"Retained literature shard missing: {relative}")
        description = index.describe(path)
        if (description["sha256"] != entry.get("sha256")
                or description["bytes"] != entry.get("bytes")):
            raise ValueError(f"Retained literature shard checksum mismatch: {relative}")
        expected.add(path)
        retained[relative] = retired
    return retained

PAPERS_PREFIXES = (
    "research/articles/",
    "zh/research/articles/",
    "fr/research/articles/",
    "research/diseases/",
    "zh/research/diseases/",
    "fr/research/diseases/",
    "research/catalogue/diseases/",
    "research/catalogue/studies/",
    "research/cards/",
    "site-data/research/",
)

_PAPERS_ROOT_PATH_RE = re.compile(
    r"^/(?:zh/|fr/)?research/(?:articles|diseases)/"
    r"|^/research/catalogue/(?:diseases|studies)/"
    r"|^/research/cards/"
    r"|^/site-data/research/"
)

_ATTR_ROOT_REF_RE = re.compile(
    r"""(?P<prefix>\b(?:href|src|content|poster|data-graph-src|data-src)\s*=\s*["'])"""
    r"""(?P<url>/[^"']*)"""
    r"""(?P<suffix>["'])""",
    re.IGNORECASE,
)

_ABS_MAIN_PAPERS_RE = re.compile(
    r"""(?P<prefix>\b(?:href|src|content|poster)\s*=\s*["'])"""
    r"""(?P<origin>https?://(?:www\.)?globalinfectiousdisease\.com)"""
    r"""(?P<path>/(?:zh/|fr/)?research/(?:articles|diseases)/[^"']*|/research/catalogue/(?:diseases|studies)/[^"']*|/research/cards/[^"']*|/site-data/research/[^"']*)"""
    r"""(?P<suffix>["'])""",
    re.IGNORECASE,
)

PAPERS_REDIRECT_RULES = (
    "/research/articles/*  {papers}/research/articles/:splat  301",
    "/zh/research/articles/*  {papers}/zh/research/articles/:splat  301",
    "/fr/research/articles/*  {papers}/fr/research/articles/:splat  301",
    "/research/diseases/*  {papers}/research/diseases/:splat  301",
    "/zh/research/diseases/*  {papers}/zh/research/diseases/:splat  301",
    "/fr/research/diseases/*  {papers}/fr/research/diseases/:splat  301",
    "/research/catalogue/diseases/*  {papers}/research/catalogue/diseases/:splat  301",
    "/research/catalogue/studies/*  {papers}/research/catalogue/studies/:splat  301",
    "/research/cards/*  {papers}/research/cards/:splat  301",
    "/site-data/research/*  {papers}/site-data/research/:splat  301",
)

# (path, also needs Methods/Headers) for main-site files the papers host fetches.
PAPERS_CORS_PATHS = (
    ("/_astro/*", True),
    ("/scripts/*", True),
    ("/site-data/*", True),
    ("/release.json", True),
    ("/logo.png", False),
    ("/logo-mark-128.png", False),
    ("/logo-1-256.webp", False),
    ("/logo-2-256.webp", False),
)

PAPERS_CORS_ORIGINS_HEADER = "Access-Control-Allow-Origin: {papers}"
PAPERS_CORS_METHODS_HEADER = "Access-Control-Allow-Methods: GET, HEAD, OPTIONS"
PAPERS_CORS_HEADERS_HEADER = "Access-Control-Allow-Headers: Content-Type"


@dataclass(frozen=True)
class PapersBundleResult:
    """Outcome of splitting one Astro ``dist`` tree."""

    main_file_count: int
    papers_file_count: int
    moved_paths: list[str]
    papers_dir: Path
    main_dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class PagesBudgetResult:
    """Pages file-count budget evaluation for the slim main tree."""

    file_count: int
    budget: int
    warn_at: int
    ok: bool
    warn: bool


def is_papers_relative_path(relative_path: str) -> bool:
    """Return True when a dist-relative path belongs on the papers host."""

    normalized = relative_path.replace("\\", "/").lstrip("./")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in PAPERS_PREFIXES)


def is_papers_root_url(url: str) -> bool:
    """Return True when a root-relative URL stays on the papers host."""

    path = url.split("?", 1)[0].split("#", 1)[0]
    return bool(_PAPERS_ROOT_PATH_RE.match(path))


def rewrite_papers_html(
    html: str,
    *,
    main_origin: str = DEFAULT_MAIN_SITE_ORIGIN,
    papers_origin: str = DEFAULT_PAPERS_ORIGIN,
) -> str:
    """Keep versioned build assets on the papers host and navigation on main.

    Papers-owned paths stay root-relative (same host after R2 upload). Absolute
    main-site URLs that point at papers content are rewritten to ``papers_origin``.
    """

    main = main_origin.rstrip("/")
    papers = papers_origin.rstrip("/")

    def _attr(match: re.Match[str]) -> str:
        url = match.group("url")
        if is_papers_root_url(url) or url.startswith("/_astro/"):
            return match.group(0)
        return f"{match.group('prefix')}{main}{url}{match.group('suffix')}"

    rewritten = _ATTR_ROOT_REF_RE.sub(_attr, html)

    def _abs(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{papers}{match.group('path')}{match.group('suffix')}"

    return _ABS_MAIN_PAPERS_RE.sub(_abs, rewritten)


def papers_redirect_rules(*, papers_origin: str = DEFAULT_PAPERS_ORIGIN) -> list[str]:
    """Wildcard Cloudflare Pages redirects that send traffic to the papers host."""

    base = papers_origin.rstrip("/")
    return [rule.format(papers=base) for rule in PAPERS_REDIRECT_RULES]


_PAPERS_REDIRECT_SOURCES = tuple(rule.split()[0] for rule in PAPERS_REDIRECT_RULES)


def strip_papers_host_redirects(redirects_text: str) -> str:
    """Remove papers-host bounces so main-origin research links stay on this site."""

    kept = []
    for line in redirects_text.splitlines():
        source = line.strip().split("  ", 1)[0].strip()
        if source in _PAPERS_REDIRECT_SOURCES:
            continue
        if line.strip() == "# Research article pages, cards, and research site-data live on the papers host.":
            continue
        kept.append(line)
    text = "\n".join(kept).strip("\n")
    return f"{text}\n" if text else ""


def append_papers_redirects(
    redirects_text: str,
    *,
    papers_origin: str = DEFAULT_PAPERS_ORIGIN,
) -> str:
    """Append papers host redirects once, idempotently."""

    rules = papers_redirect_rules(papers_origin=papers_origin)
    existing = redirects_text.rstrip()
    missing = [rule for rule in rules if rule not in existing]
    if not missing:
        return existing + ("\n" if existing else "")
    block = "\n".join(
        [
            "",
            "# Research article pages, cards, and research site-data live on the papers host.",
            *missing,
            "",
        ]
    )
    return existing + block


def ensure_papers_cors_headers(
    headers_text: str,
    *,
    papers_origin: str = DEFAULT_PAPERS_ORIGIN,
) -> str:
    """Ensure every main-site path the papers host loads allows its origin.

    Each path is checked on its own: a source ``_headers`` that already covers
    some paths must still receive the ones it lacks. Pages merges the headers of
    all matching rules, so an appended block adds CORS without replacing the
    existing cache rules.
    """

    papers = papers_origin.rstrip("/")
    marker = f"Access-Control-Allow-Origin: {papers}"

    covered: set[str] = set()
    current: str | None = None
    for line in headers_text.splitlines():
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
            current = line.strip()
        elif current and line.strip() == marker:
            covered.add(current)

    lines: list[str] = []
    for path, with_methods in PAPERS_CORS_PATHS:
        if path in covered:
            continue
        lines.extend(["", path, f"  {marker}"])
        if with_methods:
            lines.extend([f"  {PAPERS_CORS_METHODS_HEADER}", f"  {PAPERS_CORS_HEADERS_HEADER}"])
    if not lines:
        return headers_text
    comment = "# Papers host loads shared assets and release/search data cross-origin."
    return headers_text.rstrip() + "\n" + comment + "".join("\n" + item for item in lines) + "\n"


def ensure_public_shared_cors_headers(headers_text: str) -> str:
    """Allow both long-tail hosts to fetch public assets without duplicate ACAO values."""

    target_paths = {path for path, _with_methods in PAPERS_CORS_PATHS}
    lines = headers_text.splitlines()
    covered: set[str] = set()
    current: str | None = None
    for index, line in enumerate(lines):
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
            current = line.strip()
        elif current in target_paths and line.strip().lower().startswith("access-control-allow-origin:"):
            lines[index] = "  Access-Control-Allow-Origin: *"
            covered.add(current)

    added: list[str] = []
    for path, with_methods in PAPERS_CORS_PATHS:
        if path in covered:
            continue
        added.extend(["", path, "  Access-Control-Allow-Origin: *"])
        if with_methods:
            added.extend([f"  {PAPERS_CORS_METHODS_HEADER}", f"  {PAPERS_CORS_HEADERS_HEADER}"])
    result = "\n".join(lines)
    if added:
        result = result.rstrip() + "\n# Public assets used by the papers and reports hosts.\n" + "\n".join(added) + "\n"
    return result


def count_files(directory: Path) -> int:
    """Count regular files under ``directory``."""

    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*") if path.is_file())


def evaluate_pages_budget(
    main_dir: Path,
    *,
    budget: int = DEFAULT_PAGES_FILE_BUDGET,
    warn_at: int = DEFAULT_PAGES_FILE_WARN,
) -> PagesBudgetResult:
    """Evaluate the slim Pages tree against the Cloudflare file-count ceiling."""

    file_count = count_files(main_dir)
    return PagesBudgetResult(
        file_count=file_count,
        budget=budget,
        warn_at=warn_at,
        ok=file_count < budget,
        warn=file_count >= warn_at,
    )


def write_dist_inventory(directory: Path, output_path: Path) -> dict[str, int]:
    """Write ``relative_path -> size`` for every file under ``directory``."""

    inventory: dict[str, int] = {}
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            inventory[path.relative_to(directory).as_posix()] = path.stat().st_size
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return inventory


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _object_key_for_papers(relative_path: str) -> str:
    """Map a dist-relative file path to the R2 object key.

    Directory ``index.html`` files become trailing-slash keys so the papers
    custom domain serves them without rewrite rules.
    """

    normalized = relative_path.replace("\\", "/").lstrip("./")
    if normalized.endswith("/index.html"):
        return normalized[: -len("index.html")]
    if normalized == "index.html":
        return ""
    return normalized


def build_papers_manifest(
    papers_dir: Path,
    *,
    papers_origin: str = DEFAULT_PAPERS_ORIGIN,
    artifact_index: ArtifactIndex | None = None,
) -> dict:
    """Build a deterministic papers manifest for incremental R2 sync."""

    base = papers_origin.rstrip("/")
    files: list[dict[str, object]] = []
    index = artifact_index or ArtifactIndex(papers_dir)
    if papers_dir.is_dir():
        for relative, path in artifact_files(papers_dir):
            if relative == "papers-manifest.json":
                continue
            key = _object_key_for_papers(relative)
            digests = index.describe(path)
            public_path = f"/{key}" if key else "/"
            files.append(
                {
                    "path": relative,
                    "key": key,
                    **digests,
                    "url": f"{base}{public_path}",
                }
            )
    index.save()
    return {
        "schema_version": 1,
        "papers_origin": base,
        "file_count": len(files),
        "files": files,
    }


def split_papers_bundle(
    dist_dir: Path,
    *,
    papers_dir: Path,
    main_origin: str = DEFAULT_MAIN_SITE_ORIGIN,
    papers_origin: str = DEFAULT_PAPERS_ORIGIN,
    update_redirects: bool = True,
    update_headers: bool = True,
    research_on_main: bool = False,
) -> PapersBundleResult:
    """Move papers assets out of ``dist_dir`` into ``papers_dir`` and rewrite HTML."""

    if not dist_dir.is_dir():
        raise FileNotFoundError(f"Astro dist directory not found: {dist_dir}")

    papers_dir.mkdir(parents=True, exist_ok=True)
    index = ArtifactIndex(papers_dir)
    fingerprints = BuildFingerprints(index)
    manifest_path = papers_dir / "papers-manifest.json"
    try:
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous_manifest = {}
    rewrite_fingerprint = hashlib.sha256(
        Path(__file__).read_bytes() + main_origin.encode() + b"\0" + papers_origin.encode()
    ).hexdigest()
    previous = {
        item["path"]: item for item in previous_manifest.get("files", [])
    } if previous_manifest.get("rewrite_fingerprint") == rewrite_fingerprint else {}
    sources: dict[str, str] = {}
    expected: set[Path] = set()
    reused_html = 0
    pending_html = []

    moved: list[str] = []
    cards_enabled = research_social_cards_enabled()
    for relative, path in artifact_files(dist_dir):
        if not is_papers_relative_path(relative):
            continue
        if not cards_enabled and is_research_card(relative):
            # A reused/legacy build may still contain card PNGs. They belong
            # in neither the Pages output nor the next R2 upload bundle.
            path.unlink()
            continue
        destination = papers_dir / relative
        expected.add(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".html"):
            source_hash = fingerprints.sha256(path)
            sources[relative] = source_hash
            cached = previous.get(relative) or {}
            if (cached.get("source_sha256") == source_hash and destination.is_file()
                    and index.describe(destination)["sha256"] == cached.get("sha256")):
                reused_html += 1
                path.unlink()
            else:
                pending_html.append((path, destination, source_hash))
        else:
            source_hash = fingerprints.sha256(path)
            if destination.is_file() and index.describe(destination)["sha256"] == source_hash:
                path.unlink()
            else:
                shutil.move(str(path), str(destination))
        moved.append(relative)

    rewrite_changed_pages(pending_html, index, kind="papers",
                          origins={"main_origin": main_origin, "papers_origin": papers_origin})

    # Astro emits the papers host's own robots.txt and sitemap.xml under
    # dist/papers-host/; they belong at the root of the papers host.
    host_dir = dist_dir / PAPERS_HOST_DIR
    if host_dir.is_dir():
        for path in sorted(host_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = relative_key(path, host_dir)
            destination = papers_dir / relative
            expected.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(f"{PAPERS_HOST_DIR}/{relative}")

    assets = copy_bundle_assets(dist_dir, papers_dir, [path for path in expected if path.suffix == ".html"], artifact_index=index)
    expected.update(assets)
    current_assets = sorted(relative_key(path, papers_dir) for path in assets)
    generations = [current_assets]
    generations.extend(group for group in previous_manifest.get("asset_generations", []) if group != current_assets)
    generations = generations[:2]
    for generation in generations:
        for relative in generation:
            path = papers_dir / relative
            if path.resolve().is_relative_to(papers_dir.resolve()) and path.is_file():
                expected.add(path)

    library_shard_retirements = retain_library_shards(
        papers_dir, expected, previous_manifest, index,
    )

    # Remove emptied directories left behind under dist.
    for directory in sorted(
        (path for path in dist_dir.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

    if update_redirects:
        redirects_path = dist_dir / "_redirects"
        current = redirects_path.read_text(encoding="utf-8") if redirects_path.exists() else ""
        # The Pages worker serves /research/* on the main origin. Papers-host
        # 301s would make weekly and catalogue links bounce to the old host.
        updated = (
            strip_papers_host_redirects(current)
            if research_on_main
            else append_papers_redirects(current, papers_origin=papers_origin)
        )
        redirects_path.write_text(updated, encoding="utf-8")

    if update_headers:
        headers_path = dist_dir / "_headers"
        current = headers_path.read_text(encoding="utf-8") if headers_path.exists() else ""
        headers_path.write_text(
            ensure_papers_cors_headers(current, papers_origin=papers_origin),
            encoding="utf-8",
        )

    for stale in papers_dir.rglob("*"):
        if stale.is_file() and stale not in expected and stale != manifest_path:
            stale.unlink()
    manifest = build_papers_manifest(papers_dir, papers_origin=papers_origin, artifact_index=index)
    manifest["rewrite_fingerprint"] = rewrite_fingerprint
    manifest["asset_generations"] = generations
    manifest["local_build_assets"] = True
    manifest["library_shard_retirements"] = library_shard_retirements
    for item in manifest["files"]:
        if item["path"] in sources:
            item["source_sha256"] = sources[item["path"]]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Papers split: reused {reused_html}/{len(sources)} rewritten HTML files", flush=True)

    return PapersBundleResult(
        main_file_count=count_files(dist_dir),
        papers_file_count=count_files(papers_dir) - (1 if manifest_path.exists() else 0),
        moved_paths=moved,
        papers_dir=papers_dir,
        main_dir=dist_dir,
        manifest_path=manifest_path,
    )


def assert_pages_budget(
    main_dir: Path,
    *,
    budget: int = DEFAULT_PAGES_FILE_BUDGET,
    warn_at: int = DEFAULT_PAGES_FILE_WARN,
) -> PagesBudgetResult:
    """Raise when the slim Pages tree would exceed Cloudflare's file ceiling."""

    result = evaluate_pages_budget(main_dir, budget=budget, warn_at=warn_at)
    if not result.ok:
        raise RuntimeError(
            f"Cloudflare Pages file budget exceeded: {result.file_count} files "
            f"(limit {result.budget}). Split papers assets before deploying."
        )
    return result


def public_origin_from_url(url: str | None, fallback: str) -> str:
    """Normalize a configured public origin or return ``fallback``."""

    raw = (url or "").strip()
    if not raw:
        return fallback.rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return fallback.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
