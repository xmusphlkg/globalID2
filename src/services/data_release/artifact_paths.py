"""Cheap lexical keys for files enumerated beneath an artifact directory."""
from __future__ import annotations

import os
from pathlib import Path


def relative_key(path: Path, root: Path) -> str:
    # Callers enumerate their own roots. Security checks on external references
    # still resolve symlinks separately in bundle_assets / BundleAssetGraph.
    return os.path.relpath(path, root).replace(os.sep, "/")


def artifact_files(root: Path):
    rows = []
    for parent, _directories, files in os.walk(root):
        for name in files:
            path = os.path.join(parent, name)
            if os.path.isfile(path):
                rows.append((os.path.relpath(path, root).replace(os.sep, "/"), path))
    for relative, path in sorted(rows):
        yield relative, Path(path)
