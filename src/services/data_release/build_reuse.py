"""Reuse locally validated, already split publication artifacts across tasks.

This is a build receipt, never proof of remote publication or permission to
acknowledge database changes. The ordinary publication/retry path still runs.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .artifact_index import ArtifactIndex


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def stamp_homepages(dist: Path, identity: dict) -> bool:
    """Update provenance only; missing markers make the build non-reusable."""
    complete = True
    for relative in ("index.html", "fr/index.html", "zh/index.html"):
        path = dist / relative
        if not path.is_file():
            complete = False
            continue
        body = path.read_text(encoding="utf-8")
        body, count = re.subn(r'(<html\b[^>]*\bdata-release-id=")[^"]*(")',
            lambda m: m[1] + html.escape(str(identity["release_id"]), quote=True) + m[2], body)
        complete &= count == 1
        for name, field in (("gids-source-commit", "source_commit"), ("gids-deployment-branch", "deployment_branch")):
            pattern = r'<meta\b(?=[^>]*\bname="' + name + r'")[^>]*>'
            tags = list(re.finditer(pattern, body))
            # Astro compresses empty attributes to a bare `content` token.
            content = r"(\scontent)(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)"
            if len(tags) != 1 or not re.search(content, tags[0][0]):
                complete = False
                continue
            tag = re.sub(content,
                lambda m: m[1] + '="' + html.escape(str(identity[field]), quote=True) + '"', tags[0][0])
            body = body[:tags[0].start()] + tag + body[tags[0].end():]
        if body != path.read_text(encoding="utf-8"):
            path.write_text(body, encoding="utf-8")
    return complete


class BuildReuse:
    def __init__(self, root: Path, *, split: bool, configuration: dict, node_version: str | None = None):
        self.root = root.resolve()
        self.astro = self.root / "astro-site"
        self.outputs = [self.astro / "dist"]
        if split:
            self.outputs += [self.root / "exports/papers-site", self.root / "exports/reports-site"]
        self.configuration = configuration
        self.path = self.root / "data/cache/validated-site-build.json"
        self.index = ArtifactIndex(self.root, cache_dir=self.root / "data/cache/build-reuse-index", resolve_keys=False)
        local_node = self.astro / "node_modules/.bin/node"
        self.node_version = node_version if node_version is not None else subprocess.check_output(
            [str(local_node) if local_node.is_file() else "node", "--version"], text=True, timeout=10).strip()
        self.reason = "missing-receipt"

    def _files(self, root: Path, *, outputs: bool):
        if root.is_symlink():
            raise ValueError(f"Symlink in build inputs/outputs: {root}")
        if root.is_file():
            yield root
        elif root.is_dir():
            for parent, dirs, files in os.walk(root):
                dirs[:] = sorted(name for name in dirs if name != ".git" and (outputs or (
                    name != "__pycache__" and Path(parent) / name != self.astro / "src/data/_release-projection")))
                if any((Path(parent) / name).is_symlink() for name in dirs):
                    raise ValueError(f"Symlink directory in build inputs/outputs: {parent}")
                for name in sorted(files):
                    path = Path(parent) / name
                    if path.is_symlink():
                        raise ValueError(f"Symlink in build inputs/outputs: {path}")
                    if name != ".git":
                        yield path

    def _tree(self, roots: list[Path], *, outputs: bool = False) -> str:
        digest = hashlib.sha256()
        for root in roots:
            digest.update(str(root.relative_to(self.root)).encode() + b"\0")
            digest.update(b"present" if root.exists() else b"missing")
            paths = [path for path in self._files(root, outputs=outputs)
                     if path != self.root / "configs/literature/weekly_ai_reviews.json"
                     and not (outputs and path == self.astro / "dist/release.json")]
            # Warm receipts need metadata only. Hash independent cache misses on
            # multiple cores without making every warm lookup a thread-pool job.
            cached = {path: self.index.cached(path) for path in paths}
            missing = [path for path in paths if cached[path] is None]
            if missing:
                with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
                    cached.update(zip(missing, pool.map(self.index.describe, missing)))
            for path in paths:
                digest.update(path.relative_to(self.root).as_posix().encode() + b"\0")
                digest.update(bytes.fromhex(cached[path]["sha256"]))
        return digest.hexdigest()

    def inputs(self) -> dict:
        ignored_identity = {"PUBLIC_GIDS_RELEASE_ID", "PUBLIC_GIDS_SOURCE_COMMIT", "PUBLIC_GIDS_SOURCE_BRANCH",
                            "PUBLIC_GIDS_DEPLOY_BRANCH", "PUBLIC_GIDS_BUILT_AT"}
        environment = {key: value for key, value in os.environ.items()
                       if (key.startswith("PUBLIC_") and key not in ignored_identity) or key in {
                           "GLOBALID_SITE_URL", "SITE_URL", "SITUATION_SHADOW_PREVIEW", "NODE_ENV", "TZ",
                           "GIDS_PAGES_DATA_WORKER", "GIDS_RUNTIME_DATA_READY", "GIDS_RUNTIME_SITUATION_READY",
                           "GIDS_EXTERNAL_SURVEILLANCE_DATA",
                           "SUBSCRIPTIONS__PUBLIC_BASE_URL"}}
        return {
            "tree": self._tree([self.root / name for name in (
                "src", "scripts", "configs", "shared", "astro-site/src", "astro-site/scripts", "astro-site/public",
                "cloudflare/surveillance-data/src", "cloudflare/surveillance-data/scripts",
                "cloudflare/surveillance-data/package.json", "cloudflare/surveillance-data/package-lock.json",
                "cloudflare/surveillance-data/wrangler.jsonc",
                "astro-site/node_modules/.package-lock.json", "requirements.txt", "CHANGELOG.md", "data/system-settings.json")]
                + (sorted(path for path in self.astro.iterdir() if path.is_file()) if self.astro.is_dir() else [])),
            "configuration": _digest(self.configuration), "environment": _digest(environment),
            "node": self.node_version, "roots": [str(path) for path in self.outputs],
            "day": datetime.now(timezone.utc).date().isoformat(),
        }

    def matches(self, inputs: dict) -> bool:
        try:
            saved = json.loads(self.path.read_text())
            if saved.get("schema") != 1 or saved.get("inputs") != inputs:
                self.reason = "changed-inputs"
                return False
            if not all(path.is_dir() for path in self.outputs):
                self.reason = "missing-partition"
                return False
            if saved.get("outputs") != self._tree(self.outputs, outputs=True):
                self.reason = "changed-outputs"
                return False
            self.reason = "validated-partitions"
            return True
        except (OSError, ValueError, TypeError, AttributeError):
            self.reason = "missing-or-invalid-receipt"
            return False

    def save(self, inputs: dict, *, stamped: bool) -> bool:
        if inputs != self.inputs():
            self.reason = "changed-during-build"
            return False
        if not stamped or not all(path.is_dir() for path in self.outputs):
            return False
        payload = {"schema": 1, "inputs": inputs, "outputs": self._tree(self.outputs, outputs=True)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".validated-site-build-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(payload, stream, separators=(",", ":"))
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.index.save()
        return True
