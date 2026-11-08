"""Copy the transitive static dependencies of independently hosted HTML."""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path

from .artifact_index import ArtifactIndex, content_digests

ASSET_SUFFIXES = {".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".ico"}
ASSET_TOKEN_RE = re.compile(r'''["'`(]([^"'`()\s<>]+)["'`)]''')
REFERENCE_SCHEMA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _asset_references(path: Path, index: ArtifactIndex, *, body: bytes | None = None) -> list[str]:
    """Reuse parsed references only when the artifact and parser are unchanged."""
    key = index.key(path)
    digests = index.cached(path) if body is None else content_digests(body)
    entry = index.entries.get(key, {})
    parsed = entry.get("asset_references", {})
    if not isinstance(parsed, dict):
        parsed = {}
    valid = (parsed.get("schema") == REFERENCE_SCHEMA and isinstance(parsed.get("tokens"), list)
             and all(isinstance(token, str) for token in parsed["tokens"]))
    if valid and digests is not None and parsed.get("sha256") == digests["sha256"]:
        return parsed["tokens"]
    if body is None:
        body, digests = index.read(path)
    if valid and parsed.get("sha256") == digests["sha256"]:
        index.entries[key]["asset_references"] = parsed
        return parsed["tokens"]
    tokens = [token for token in ASSET_TOKEN_RE.findall(body.decode("utf-8"))
              if Path(token.split("?", 1)[0].split("#", 1)[0]).suffix.lower() in ASSET_SUFFIXES]
    index.entries[key]["asset_references"] = {
        "schema": REFERENCE_SCHEMA, "sha256": digests["sha256"], "tokens": tokens,
    }
    return tokens

def copy_bundle_assets(dist_dir: Path, bundle_dir: Path, pages: list[Path], *, artifact_index: ArtifactIndex | None = None) -> set[Path]:
    """Follow generated import/CSS references, including Astro hydration attributes.

    Assets are copied, never moved: the main site can use the same build too.
    Only files referenced by bundle pages and their dependencies enter R2.
    """
    root = dist_dir.resolve()
    index = artifact_index or ArtifactIndex(bundle_dir)
    from .bundle_asset_graph import BundleAssetGraph
    graph = BundleAssetGraph(dist_dir, bundle_dir, pages, index, REFERENCE_SCHEMA)
    restored = graph.restore()
    if restored is not None:
        print(f"Bundle assets: reused verified dependency graph for {len(pages)} HTML pages", flush=True)
        return restored
    observed = {}
    from .bundle_asset_workers import warm_asset_references
    warm_asset_references(pages, index)
    pending: list[tuple[bytes | None, str]] = [(None, page.relative_to(bundle_dir).as_posix()) for page in pages]
    copied: set[Path] = set()
    visited: set[str] = set()
    while pending:
        content, owner = pending.pop()
        for token in _asset_references(bundle_dir / owner, index, body=content):
            ref = token.split("?", 1)[0].split("#", 1)[0]
            if not ref or ":" in ref or ref.startswith("//") or Path(ref).suffix.lower() not in ASSET_SUFFIXES:
                continue
            relative = ref.lstrip("/") if ref.startswith(("/", "_astro/")) else posixpath.normpath(posixpath.join(posixpath.dirname(owner), ref))
            if relative in visited:
                continue
            source = (dist_dir / relative).resolve()
            if not source.is_relative_to(root):
                raise ValueError(f"Bundle asset escapes build directory: {ref}")
            if not source.is_file():
                observed[relative] = None
                if ref.startswith("/_astro/") or (owner.startswith("_astro/") and ref.startswith(("./", "../"))):
                    raise FileNotFoundError(f"Bundle asset missing: {owner} -> {ref}")
                continue
            visited.add(relative)
            target = bundle_dir / relative
            if target in copied:
                continue
            copied.add(target)
            body, digests = index.read(source)
            observed[relative] = digests["sha256"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or index.describe(target)["sha256"] != content_digests(body)["sha256"]:
                index.write(target, body)
            if source.suffix in {".js", ".css"}:
                pending.append((body, relative))
    graph.save(observed, copied)
    return copied
