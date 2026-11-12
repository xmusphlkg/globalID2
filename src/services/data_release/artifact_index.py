"""Local build receipts: reuse digests without rereading unchanged artifacts.

Receipts live outside deployable directories. They are hints, never authority:
an inode/size/mtime/ctime change or a missing receipt requires reading the file.
Build writers record bytes they already hold; publishers validate those receipts
against the manifest and still reconcile actual R2 object metadata on every run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path


def _signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def content_digests(body: bytes) -> dict:
    return {
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "md5": hashlib.md5(body, usedforsecurity=False).hexdigest(),
    }


class ArtifactIndex:
    def __init__(self, root: Path, *, cache_dir: Path | None = None, resolve_keys: bool = False):
        self.resolve_keys = resolve_keys
        self.root = root.resolve()
        directory = cache_dir or Path(__file__).resolve().parents[3] / "data/cache/artifact-index"
        key = hashlib.sha256(str(self.root).encode()).hexdigest()
        self.path = directory / f"{key}.json"
        try:
            value = json.loads(self.path.read_text())
            self.entries = value["files"] if isinstance(value, dict) and value.get("schema") == 1 and isinstance(value.get("files"), dict) else {}
        except (OSError, ValueError, TypeError):
            self.entries = {}
        self.read_files = 0
        self.read_bytes = 0

    def key(self, path: Path) -> str:
        return str(path.resolve() if self.resolve_keys else path.absolute())

    def cached(self, path: Path) -> dict | None:
        entry = self.entries.get(self.key(path))
        if not isinstance(entry, dict) or not entry.get("stable"):
            return None
        try:
            if entry.get("stat") != _signature(path):
                return None
        except FileNotFoundError:
            return None
        if not all(isinstance(entry.get(key), str) and len(entry[key]) == length
                   for key, length in (("sha256", 64), ("md5", 32))):
            return None
        if not isinstance(entry.get("bytes"), int):
            return None
        return {key: entry[key] for key in ("bytes", "sha256", "md5")}

    def record(self, path: Path, body: bytes) -> dict:
        digests = content_digests(body)
        signature = _signature(path)
        if signature[2] != len(body):
            raise ValueError(f"Artifact changed while recording: {path}")
        # Filesystems may round timestamps: two same-size writes in one clock
        # tick can have identical metadata. Like Git's racy-stat protection,
        # don't trust a receipt captured in the file's current timestamp window.
        stable = max(signature[3:]) < time.time_ns() - 1_000_000_000
        self.entries[self.key(path)] = {**digests, "stat": signature, "stable": stable}
        return digests

    def cached_representation(self, path: Path, recipe: str) -> dict | None:
        """Reuse wire digests only while the underlying file receipt is valid."""
        source = self.cached(path)
        if source is None:
            return None
        entry = self.entries.get(self.key(path), {})
        representation = entry.get("representation")
        if not isinstance(representation, dict) or representation.get("recipe") != recipe:
            return None
        if (representation.get("source_sha256") != source["sha256"]
                or type(representation.get("bytes")) is not int
                or representation["bytes"] < 0
                or not isinstance(representation.get("md5"), str)
                or re.fullmatch(r"[0-9a-f]{32}", representation["md5"]) is None
                or representation.get("encoding") not in (None, "gzip")):
            return None
        return dict(representation)

    def record_representation(
        self, path: Path, recipe: str, source: dict, body: bytes, *, encoding: str | None,
    ) -> None:
        """Bind a deterministic representation to the source read that made it.

        Each worker replaces its entry rather than mutating shared nested data.
        Concurrent cache writers may lose a hint, but cannot combine receipts.
        """
        key = self.key(path)
        entry = self.entries.get(key)
        if (not isinstance(entry, dict) or entry.get("stat") != _signature(path)
                or any(entry.get(field) != source.get(field) for field in ("bytes", "sha256", "md5"))):
            return
        self.entries[key] = {
            **entry,
            "representation": {
                "recipe": recipe, "source_sha256": source["sha256"],
                "bytes": len(body), "md5": hashlib.md5(body, usedforsecurity=False).hexdigest(),
                "encoding": encoding,
            },
        }

    def read(self, path: Path) -> tuple[bytes, dict]:
        before = _signature(path)
        body = path.read_bytes()
        if before != _signature(path):
            raise ValueError(f"Artifact changed while reading: {path}")
        digests = self.record(path, body)
        self.read_files += 1
        self.read_bytes += len(body)
        return body, digests

    def describe(self, path: Path) -> dict:
        cached = self.cached(path)
        if cached is None:
            _, cached = self.read(path)
        return cached

    def write(self, path: Path, body: bytes) -> None:
        path.write_bytes(body)
        self.record(path, body)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Missing/stale receipts are harmless after concurrent cache writers or
        # a crash. Atomic replacement prevents a partially written cache file.
        fd, name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"schema": 1, "files": self.entries}, handle, separators=(",", ":"))
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
