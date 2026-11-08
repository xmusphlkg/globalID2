"""Reuse a complete bundle's verified asset closure, including absent targets."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from .artifact_paths import relative_key


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class BundleAssetGraph:
    def __init__(self, dist, bundle, pages, index, version):
        self.dist, self.bundle, self.index = dist.resolve(), bundle.resolve(), index
        self.path = index.path.with_suffix(".assets.json")
        self.version = _hash([version, Path(__file__).read_text()])
        self.pages = _hash([(relative_key(path, bundle), index.describe(path)["sha256"]) for path in sorted(pages, key=str)])

    def source(self, relative):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"Invalid bundle asset path: {relative}")
        path = (self.dist / relative).resolve()
        if not path.is_relative_to(self.dist):
            raise ValueError(f"Bundle asset escapes build directory: {relative}")
        return path

    def restore(self):
        try:
            record = json.loads(self.path.read_text())
            data = record["data"]
            if record["checksum"] != _hash(data) or data["version"] != self.version or data["pages"] != self.pages:
                return None
            sources, assets = data["sources"], data["assets"]
            if not isinstance(sources, dict) or not isinstance(assets, list) or not set(assets).issubset(sources):
                return None
            for relative, digest in sources.items():
                path = self.source(relative)
                current = self.index.describe(path)["sha256"] if path.is_file() else None
                if current != digest:
                    return None
            # Validate every dependency before modifying a destination. Missing
            # files that become available also invalidate the graph above.
            copied = set()
            for relative in assets:
                if sources[relative] is None:
                    return None
                source, target = self.source(relative), self.bundle / relative
                if not target.is_file() or self.index.describe(target)["sha256"] != sources[relative]:
                    body, digests = self.index.read(source)
                    if digests["sha256"] != sources[relative]:
                        raise ValueError(f"Asset changed during graph reuse: {relative}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    self.index.write(target, body)
                copied.add(target)
            return copied
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(self, sources, copied):
        data = {"version": self.version, "pages": self.pages, "sources": sources,
                "assets": sorted(relative_key(path, self.bundle) for path in copied)}
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"data": data, "checksum": _hash(data)}, separators=(",", ":")))
            temporary.replace(self.path)
        except OSError:
            logging.getLogger(__name__).warning("Could not save optional bundle asset graph", exc_info=True)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
