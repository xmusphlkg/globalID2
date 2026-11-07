"""Read strong build-time digests without rereading verified rendered bytes."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .artifact_index import ArtifactIndex, _signature


class BuildFingerprints:
    def __init__(self, index: ArtifactIndex, cache_path: Path | None = None):
        self.index = index
        cache_path = cache_path or Path(__file__).resolve().parents[3] / "data/cache/build-file-fingerprints.json"
        try:
            record = json.loads(cache_path.read_text())
            self.files = record["files"] if record.get("schema") == 1 and isinstance(record.get("files"), dict) else {}
        except (OSError, ValueError, TypeError, AttributeError):
            self.files = {}

    def sha256(self, path: Path) -> str:
        entry = self.files.get(str(path.absolute()))
        signature = _signature(path)
        if (isinstance(entry, dict) and entry.get("stable") is True
                and isinstance(entry.get("sha256"), str)
                and re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                and max(signature[3:]) < time.time_ns() - 1_000_000_000
                and entry.get("stat") == ":".join(map(str, signature))):
            return entry["sha256"]
        return self.index.describe(path)["sha256"]

    def read(self, path: Path, expected: str) -> bytes:
        body, digests = self.index.read(path)
        if digests["sha256"] != expected:
            raise ValueError(f"Build artifact changed during partitioning: {path}")
        return body
