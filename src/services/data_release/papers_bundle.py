"""Split Astro dist into Pages main + papers (R2) bundles.

Long-tail research article pages, card PNGs, and ``site-data/research`` shards
move to the papers host so Cloudflare Pages stays under its file-count ceiling.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .bundle_assets import copy_bundle_assets

DEFAULT_MAIN_SITE_ORIGIN = "https://globalinfectiousdisease.com"
DEFAULT_PAPERS_ORIGIN = "https://papers.globalinfectiousdisease.com"
DEFAULT_PAGES_FILE_BUDGET = 20_000
DEFAULT_PAGES_FILE_WARN = 18_000

# Directory in dist holding files that live at the root of the papers host.
PAPERS_HOST_DIR = "papers-host"

PAPERS_PREFIXES = (
    "research/articles/",
    "zh/research/articles/",
    "fr/research/articles/",
    "research/cards/",
    "site-data/research/",
)

_PAPERS_ROOT_PATH_RE = re.compile(
    r"^/(?:zh/|fr/)?research/articles/"
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
    r"""(?P<path>/(?:zh/|fr/)?research/articles/[^"']*|/research/cards/[^"']*|/site-data/research/[^"']*)"""
    r"""(?P<suffix>["'])""",
    re.IGNORECASE,
)

PAPERS_REDIRECT_RULES = (
    "/research/articles/*  {papers}/research/articles/:splat  301",
    "/zh/research/articles/*  {papers}/zh/research/articles/:splat  301",
    "/fr/research/articles/*  {papers}/fr/research/articles/:splat  301",
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
) -> dict:
    """Build a deterministic papers manifest for incremental R2 sync."""

    base = papers_origin.rstrip("/")
    files: list[dict[str, object]] = []
    if papers_dir.is_dir():
        for path in sorted(papers_dir.rglob("*")):
            if not path.is_file() or path == papers_dir / "papers-manifest.json":
                continue
            relative = path.relative_to(papers_dir).as_posix()
            key = _object_key_for_papers(relative)
            size = path.stat().st_size
            digest = _sha256_file(path)
            public_path = f"/{key}" if key else "/"
            files.append(
                {
                    "path": relative,
                    "key": key,
                    "bytes": size,
                    "sha256": digest,
                    "url": f"{base}{public_path}",
                }
            )
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
) -> PapersBundleResult:
    """Move papers assets out of ``dist_dir`` into ``papers_dir`` and rewrite HTML."""

    if not dist_dir.is_dir():
        raise FileNotFoundError(f"Astro dist directory not found: {dist_dir}")

    papers_dir.mkdir(parents=True, exist_ok=True)
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

    moved: list[str] = []
    for path in sorted(dist_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(dist_dir).as_posix()
        if not is_papers_relative_path(relative):
            continue
        destination = papers_dir / relative
        expected.add(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".html"):
            body = path.read_bytes()
            source_hash = hashlib.sha256(body).hexdigest()
            sources[relative] = source_hash
            cached = previous.get(relative) or {}
            if (cached.get("source_sha256") == source_hash and destination.is_file()
                    and _sha256_file(destination) == cached.get("sha256")):
                reused_html += 1
            else:
                destination.write_text(
                    rewrite_papers_html(
                        body.decode("utf-8"),
                        main_origin=main_origin,
                        papers_origin=papers_origin,
                    ),
                    encoding="utf-8",
                )
            path.unlink()
        else:
            shutil.move(str(path), str(destination))
        moved.append(relative)

    # Astro emits the papers host's own robots.txt and sitemap.xml under
    # dist/papers-host/; they belong at the root of the papers host.
    host_dir = dist_dir / PAPERS_HOST_DIR
    if host_dir.is_dir():
        for path in sorted(host_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(host_dir).as_posix()
            destination = papers_dir / relative
            expected.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(f"{PAPERS_HOST_DIR}/{relative}")

    assets = copy_bundle_assets(dist_dir, papers_dir, [path for path in expected if path.suffix == ".html"])
    expected.update(assets)
    current_assets = sorted(path.relative_to(papers_dir).as_posix() for path in assets)
    generations = [current_assets]
    generations.extend(group for group in previous_manifest.get("asset_generations", []) if group != current_assets)
    generations = generations[:2]
    for generation in generations:
        for relative in generation:
            path = papers_dir / relative
            if path.resolve().is_relative_to(papers_dir.resolve()) and path.is_file():
                expected.add(path)

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
        redirects_path.write_text(
            append_papers_redirects(current, papers_origin=papers_origin),
            encoding="utf-8",
        )

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
    manifest = build_papers_manifest(papers_dir, papers_origin=papers_origin)
    manifest["rewrite_fingerprint"] = rewrite_fingerprint
    manifest["asset_generations"] = generations
    manifest["local_build_assets"] = True
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
