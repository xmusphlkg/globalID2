"""Optional, disposable cache of pure title associations, never publication state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def title_cache_identity(*, diseases: dict, aliases: dict, implementation: bytes, prepared: list) -> str:
    """Include labels/URLs and the actual alias order as well as matcher code."""
    return hashlib.sha256(_encoded({
        "diseases": diseases,
        "aliases": aliases,
        "implementation": hashlib.sha256(implementation).hexdigest(),
        "prepared": [(did, raw, term, pattern.pattern, pattern.flags) for did, raw, term, pattern in prepared],
    })).hexdigest()


class LiteratureTitleCache:
    SCHEMA = 1

    def __init__(self, path: Path | None, identity: str):
        self.path = path
        self.identity = identity
        self.previous: dict[str, list[dict[str, Any]]] = {}
        self.current: dict[str, list[dict[str, Any]]] = {}
        self.hits = 0
        self.misses = 0
        if path is None:
            return
        try:
            envelope = json.loads(path.read_bytes())
            payload = envelope["payload"]
            if (payload["schema"] != self.SCHEMA or payload["identity"] != identity
                    or hashlib.sha256(_encoded(payload)).hexdigest() != envelope["sha256"]):
                return
            entries = payload["entries"]
            if not isinstance(entries, dict) or not all(
                isinstance(key, str) and isinstance(value, list)
                and all(isinstance(link, dict) for link in value)
                for key, value in entries.items()
            ):
                return
            self.previous = entries
        except (OSError, ValueError, TypeError, KeyError):
            # A cache is an optimization, never an authoritative input.
            return

    def links(self, article_id: str, title: str, compute: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        key = hashlib.sha256(_encoded([article_id, title])).hexdigest()
        if key in self.previous:
            value = self.previous[key]
            self.hits += 1
        else:
            value = compute()
            self.misses += 1
        self.current[key] = value
        # The caller may merge/enrich links; cached values must remain pure.
        return [dict(link) for link in value]

    def save(self) -> None:
        """Replace with this snapshot's eligible IDs, pruning deleted/ineligible rows."""
        if self.path is None:
            return
        payload = {"schema": self.SCHEMA, "identity": self.identity, "entries": self.current}
        body = _encoded({"payload": payload, "sha256": hashlib.sha256(_encoded(payload)).hexdigest()})
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(body)
            os.replace(temporary, self.path)
        except OSError:
            # Read-only/full cache storage must not block a correct export.
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
