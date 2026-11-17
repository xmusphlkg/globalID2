"""Small, atomic SINAN parse caches keyed by verified source content."""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from src.core import get_logger

logger = get_logger(__name__)


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


class SINANContentCache:
    """Cache scope and parser version are part of the identity, including empty results."""

    def __init__(self, directory, item, digest, *, version, scope="full"):
        self.key = hashlib.sha256(_encoded({
            "file": asdict(item), "sha256": digest, "version": version, "scope": scope,
        })).hexdigest()
        self.path = Path(directory) / version / (self.key + ".json")

    def read(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            result = payload["result"]
            if (payload["key"] == self.key and isinstance(result, dict)
                    and payload["result_sha256"] == hashlib.sha256(_encoded(result)).hexdigest()):
                return result
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def write(self, result):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, mode="w", encoding="utf-8", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"key": self.key, "result": result,
                           "result_sha256": hashlib.sha256(_encoded(result)).hexdigest()}, stream, ensure_ascii=False)
            temporary.replace(self.path)
        except OSError as exc:
            logger.warning(f"[BR-SINAN] unable to save parse cache | path={self.path} error={exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
