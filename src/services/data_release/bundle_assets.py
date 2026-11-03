"""Copy the transitive static dependencies of independently hosted HTML."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

ASSET_SUFFIXES = {".js", ".css", ".woff", ".woff2", ".ttf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".ico"}
ASSET_TOKEN_RE = re.compile(r'''["'`(]([^"'`()\s<>]+)["'`)]''')

def copy_bundle_assets(dist_dir: Path, bundle_dir: Path, pages: list[Path]) -> set[Path]:
    """Follow generated import/CSS references, including Astro hydration attributes.

    Assets are copied, never moved: the main site can use the same build too.
    Only files referenced by bundle pages and their dependencies enter R2.
    """
    root = dist_dir.resolve()
    pending: list[tuple[str | None, str]] = [(None, page.relative_to(bundle_dir).as_posix()) for page in pages]
    copied: set[Path] = set()
    while pending:
        content, owner = pending.pop()
        if content is None:
            content = (bundle_dir / owner).read_text(encoding="utf-8")
        for token in ASSET_TOKEN_RE.findall(content):
            ref = token.split("?", 1)[0].split("#", 1)[0]
            if not ref or ":" in ref or ref.startswith("//") or Path(ref).suffix.lower() not in ASSET_SUFFIXES:
                continue
            relative = ref.lstrip("/") if ref.startswith(("/", "_astro/")) else posixpath.normpath(posixpath.join(posixpath.dirname(owner), ref))
            source = (dist_dir / relative).resolve()
            if not source.is_relative_to(root):
                raise ValueError(f"Bundle asset escapes build directory: {ref}")
            if not source.is_file():
                if ref.startswith("/_astro/") or (owner.startswith("_astro/") and ref.startswith(("./", "../"))):
                    raise FileNotFoundError(f"Bundle asset missing: {owner} -> {ref}")
                continue
            target = bundle_dir / relative
            if target in copied:
                continue
            copied.add(target)
            body = source.read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != body:
                target.write_bytes(body)
            if source.suffix in {".js", ".css"}:
                pending.append((body.decode("utf-8"), relative))
    return copied
