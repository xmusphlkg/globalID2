"""Split generated report pages out of the Cloudflare Pages bundle."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .bundle_assets import copy_bundle_assets as _copy_report_assets
from .artifact_index import ArtifactIndex
from .build_fingerprints import BuildFingerprints
from .artifact_paths import artifact_files, relative_key
from .bundle_html_workers import rewrite_changed_pages

from .papers_bundle import (
    DEFAULT_MAIN_SITE_ORIGIN,
    DEFAULT_PAGES_FILE_BUDGET,
    DEFAULT_PAGES_FILE_WARN,
    assert_pages_budget,
    count_files,
    ensure_public_shared_cors_headers,
)

DEFAULT_REPORTS_ORIGIN = "https://reports.globalinfectiousdisease.com"
REPORTS_HOST_DIR = "reports-host"
MANIFEST_KEY = "reports-manifest.json"
REPORT_DIRECTORY_PAGE_RE = re.compile(r"^(?P<locale>(?:zh|fr)/)?reports/index\.html$")
REPORT_ROUTE_RE = re.compile(r"^(?P<locale>(?:zh|fr)/)?countries/(?P<country>[a-z0-9-]+)/reports/")
REPORT_ROOT_URL_RE = re.compile(r"^/(?:zh/|fr/)?reports(?:/|$)|^/(?:zh/|fr/)?countries/[a-z0-9-]+/reports(?:/|$)")
REPORT_HOST_CONTROL_FILES = {"robots.txt", "sitemap.xml"}
ATTR_ROOT_REF_RE = re.compile(
    r"""(?P<prefix>\b(?:href|src|content|poster|data-graph-src|data-src|action)\s*=\s*[\"'])"""
    r"""(?P<url>/[^\"']*)"""
    r"""(?P<suffix>[\"'])""",
    re.IGNORECASE,
)
ABS_MAIN_REPORT_RE = re.compile(
    r"""(?P<prefix>\b(?:href|src|content|poster)\s*=\s*[\"'])"""
    r"""(?P<origin>https?://(?:www\.)?globalinfectiousdisease\.com)"""
    r"""(?P<path>/(?:zh/|fr/)?countries/[a-z0-9-]+/reports/[^\"']*)"""
    r"""(?P<suffix>[\"'])""",
    re.IGNORECASE,
)
ABS_MAIN_REPORT_DIRECTORY_RE = re.compile(
    r"""(?P<prefix>\b(?:href|src|content|poster)\s*=\s*[\"'])"""
    r"""(?P<origin>https?://(?:www\.)?globalinfectiousdisease\.com)"""
    r"""(?P<path>/(?:zh/|fr/)?reports/[^\"']*)"""
    r"""(?P<suffix>[\"'])""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReportsBundleResult:
    main_file_count: int
    reports_file_count: int
    moved_paths: list[str]
    reports_dir: Path
    main_dir: Path
    manifest_path: Path


def is_reports_relative_path(relative_path: str) -> bool:
    """Report archive routes and reports-host control files belong on R2."""

    normalized = relative_path.replace("\\", "/").lstrip("./")
    return (
        normalized in {f"{REPORTS_HOST_DIR}/{name}" for name in REPORT_HOST_CONTROL_FILES}
        or bool(REPORT_DIRECTORY_PAGE_RE.match(normalized))
        or bool(REPORT_ROUTE_RE.match(normalized))
        or normalized.startswith("site-data/reports/")
    )


def is_reports_root_url(url: str) -> bool:
    path = url.split("?", 1)[0].split("#", 1)[0]
    return bool(REPORT_ROOT_URL_RE.match(path) or path.startswith("/site-data/reports/"))


def _reports_object_key(relative_path: str) -> str:
    """Map directory index files to the slash-suffixed keys used by the papers host."""

    normalized = relative_path.replace("\\", "/").lstrip("./")
    if normalized.endswith("/index.html"):
        return normalized[: -len("index.html")]
    return normalized


def rewrite_reports_html(
    html: str,
    *,
    main_origin: str = DEFAULT_MAIN_SITE_ORIGIN,
    reports_origin: str = DEFAULT_REPORTS_ORIGIN,
) -> str:
    """Keep report-to-report links local and send shared site links to main."""

    main = main_origin.rstrip("/")
    reports = reports_origin.rstrip("/")

    def rewrite_root(match: re.Match[str]) -> str:
        url = match.group("url")
        if is_reports_root_url(url) or url.startswith("/_astro/"):
            return match.group(0)
        return f"{match.group('prefix')}{main}{url}{match.group('suffix')}"

    rewritten = ATTR_ROOT_REF_RE.sub(rewrite_root, html)
    rewritten = ABS_MAIN_REPORT_RE.sub(
        lambda match: f"{match.group('prefix')}{reports}{match.group('path')}{match.group('suffix')}",
        rewritten,
    )
    return ABS_MAIN_REPORT_DIRECTORY_RE.sub(
        lambda match: f"{match.group('prefix')}{reports}{match.group('path')}{match.group('suffix')}",
        rewritten,
    )


def _rewrite_main_report_directory_links(dist_dir: Path, reports_origin: str) -> None:
    origin = reports_origin.rstrip("/")
    localized_urls = {
        'href="/reports/"': f'href="{origin}/reports/"',
        'href="/zh/reports/"': f'href="{origin}/zh/reports/"',
        'href="/fr/reports/"': f'href="{origin}/fr/reports/"',
    }
    for path in dist_dir.rglob("*.html"):
        body = path.read_text(encoding="utf-8")
        rewritten = body
        for source, target in localized_urls.items():
            rewritten = rewritten.replace(source, target)
        if rewritten != body:
            path.write_text(rewritten, encoding="utf-8")


def _reports_redirect_rules(report_paths: list[str], reports_origin: str) -> list[str]:
    routes: set[tuple[str, str]] = set()
    for relative in report_paths:
        match = REPORT_ROUTE_RE.match(relative)
        if not match:
            continue
        locale = match.group("locale") or ""
        country = match.group("country")
        routes.add((locale, country))
    origin = reports_origin.rstrip("/")
    rules: list[str] = []
    for locale in ("", "zh/", "fr/"):
        path = f"/{locale}reports"
        target = f"{origin}/{locale}reports"
        rules.extend([f"{path}/ {target}/ 301", f"{path}/* {target}/:splat 301"])
    rules.append(f"/site-data/reports/* {origin}/site-data/reports/:splat 301")
    for locale, country in sorted(routes):
        path = f"/{locale}countries/{country}/reports"
        target = f"{origin}/{locale}countries/{country}/reports"
        rules.extend([f"{path}/ {target}/ 301", f"{path}/* {target}/:splat 301"])
    return rules


def _include_report_directory_in_sitemap(content: str, reports_origin: str) -> str:
    origin = reports_origin.rstrip("/")
    urls = (f"{origin}/reports/", f"{origin}/zh/reports/")
    additions = [
        f"  <url><loc>{escape(url)}</loc></url>"
        for url in urls
        if f"<loc>{escape(url)}</loc>" not in content
    ]
    if not additions:
        return content
    closing = content.lower().rfind("</urlset>")
    if closing < 0:
        raise ValueError("Reports sitemap is missing its urlset closing tag")
    return content[:closing] + "\n" + "\n".join(additions) + "\n" + content[closing:]


def _append_redirects(current: str, report_paths: list[str], reports_origin: str) -> str:
    existing = current.rstrip()
    rules = list(dict.fromkeys(
        rule for rule in _reports_redirect_rules(report_paths, reports_origin) if rule not in existing
    ))
    if not rules:
        return existing + ("\n" if existing else "")
    block = "\n".join(["", "# Country report archives live on the reports host.", *rules, ""])
    return existing + block


def _manifest_for(directory: Path, reports_origin: str, *, artifact_index: ArtifactIndex | None = None) -> dict:
    index = artifact_index or ArtifactIndex(directory)
    files: list[dict[str, object]] = []
    for relative, path in artifact_files(directory):
        if path.name == MANIFEST_KEY:
            continue
        key = _reports_object_key(relative)
        public_path = "/" + key
        files.append({
            "path": relative,
            "key": key,
            **index.describe(path),
            "url": reports_origin.rstrip("/") + public_path,
        })
    index.save()
    return {
        "schema_version": 1,
        "reports_origin": reports_origin.rstrip("/"),
        "file_count": len(files),
        "files": files,
    }



def split_reports_bundle(
    dist_dir: Path,
    *,
    reports_dir: Path,
    main_origin: str = DEFAULT_MAIN_SITE_ORIGIN,
    reports_origin: str = DEFAULT_REPORTS_ORIGIN,
) -> ReportsBundleResult:
    """Move report pages into their independent R2 staging directory."""

    if not dist_dir.is_dir():
        raise FileNotFoundError(f"Astro dist directory not found: {dist_dir}")
    reports_dir.mkdir(parents=True, exist_ok=True)
    index = ArtifactIndex(reports_dir)
    fingerprints = BuildFingerprints(index)
    manifest_path = reports_dir / MANIFEST_KEY
    try:
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous_manifest = {}
    fingerprint = hashlib.sha256(
        Path(__file__).read_bytes() + main_origin.encode() + b"\0" + reports_origin.encode()
    ).hexdigest()
    previous = {
        item["path"]: item for item in previous_manifest.get("files", [])
    } if previous_manifest.get("rewrite_fingerprint") == fingerprint else {}

    moved: list[str] = []
    source_hashes: dict[str, str] = {}
    expected: set[Path] = set()
    rewritten_count = 0
    pending_html = []
    for relative, path in artifact_files(dist_dir):
        # The report-host directory is a staging wrapper: its contents become
        # the root of the reports host instead of retaining a /reports-host prefix.
        if not is_reports_relative_path(relative) or relative.startswith(REPORTS_HOST_DIR + "/"):
            continue
        destination = reports_dir / relative
        expected.add(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".html"):
            source_hash = fingerprints.sha256(path)
            source_hashes[relative] = source_hash
            cached = previous.get(relative) or {}
            if (cached.get("source_sha256") == source_hash and destination.is_file()
                    and index.describe(destination)["sha256"] == cached.get("sha256")):
                path.unlink()
            else:
                pending_html.append((path, destination, source_hash))
                rewritten_count += 1
        else:
            source_hash = fingerprints.sha256(path)
            if destination.is_file() and index.describe(destination)["sha256"] == source_hash:
                path.unlink()
            else:
                shutil.move(str(path), str(destination))
        moved.append(relative)

    rewrite_changed_pages(pending_html, index, kind="reports",
                          origins={"main_origin": main_origin, "reports_origin": reports_origin})

    host_dir = dist_dir / REPORTS_HOST_DIR
    if host_dir.is_dir():
        for name in sorted(REPORT_HOST_CONTROL_FILES):
            path = host_dir / name
            if not path.is_file():
                continue
            destination = reports_dir / name
            expected.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            if name == "sitemap.xml":
                destination.write_text(
                    _include_report_directory_in_sitemap(
                        destination.read_text(encoding="utf-8"), reports_origin,
                    ),
                    encoding="utf-8",
                )
            moved.append(f"{REPORTS_HOST_DIR}/{name}")

    report_pages = [path for path in expected if path.suffix == ".html"]
    assets = _copy_report_assets(dist_dir, reports_dir, report_pages, artifact_index=index)
    expected.update(assets)
    # Keep the preceding asset generation for cached HTML/open browser tabs.
    # The manifest makes these immutable files no-op uploads on later runs.
    current_assets = sorted(relative_key(path, reports_dir) for path in assets)
    prior_generations = previous_manifest.get("asset_generations") or []
    generations = [current_assets]
    generations.extend(group for group in prior_generations if group != current_assets)
    generations = generations[:2]
    for generation in generations:
        for relative in generation:
            path = reports_dir / relative
            if path.resolve().is_relative_to(reports_dir.resolve()) and path.is_file():
                expected.add(path)

    for directory in sorted(
        (path for path in dist_dir.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

    redirects_path = dist_dir / "_redirects"
    _rewrite_main_report_directory_links(dist_dir, reports_origin)
    redirects = redirects_path.read_text(encoding="utf-8") if redirects_path.exists() else ""
    moved_report_paths = [item for item in moved if REPORT_ROUTE_RE.match(item)]
    redirects_path.write_text(_append_redirects(redirects, moved_report_paths, reports_origin), encoding="utf-8")

    headers_path = dist_dir / "_headers"
    current_headers = headers_path.read_text(encoding="utf-8") if headers_path.exists() else ""
    # Public static site assets can be read from both long-tail hosts; wildcard
    # CORS keeps the response valid without emitting conflicting allow-origin headers.
    headers_path.write_text(ensure_public_shared_cors_headers(current_headers), encoding="utf-8")

    for stale in reports_dir.rglob("*"):
        if stale.is_file() and stale not in expected and stale != manifest_path:
            stale.unlink()

    manifest = _manifest_for(reports_dir, reports_origin, artifact_index=index)
    manifest["rewrite_fingerprint"] = fingerprint
    manifest["asset_generations"] = generations
    for item in manifest["files"]:
        if item["path"] in source_hashes:
            item["source_sha256"] = source_hashes[item["path"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Reports split: rewrote {rewritten_count}/{len(source_hashes)} HTML pages", flush=True)
    return ReportsBundleResult(
        main_file_count=count_files(dist_dir),
        reports_file_count=count_files(reports_dir) - 1,
        moved_paths=moved,
        reports_dir=reports_dir,
        main_dir=dist_dir,
        manifest_path=manifest_path,
    )


def assert_reports_pages_budget(main_dir: Path, *, budget: int = DEFAULT_PAGES_FILE_BUDGET, warn_at: int = DEFAULT_PAGES_FILE_WARN):
    return assert_pages_budget(main_dir, budget=budget, warn_at=warn_at)
