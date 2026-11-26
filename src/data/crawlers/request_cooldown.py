"""Bound repeated requests to known unavailable public source URLs.

Only failure metadata is cached. A skipped request is still a collection
failure, never a successful refresh or a replacement for archived source bytes.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

import requests


class SourceRequestDeferred(requests.RequestException):
    def __init__(self, url: str, *, retry_at: float, reason: str):
        self.retry_at = retry_at
        self.reason = reason
        super().__init__(f"Source request deferred after {reason}; retry_at={retry_at:.0f}; url={url}")


class RequestFailureCooldown:
    """Cross-process cooldowns with bounded TTL and atomic per-URL receipts."""

    def __init__(self, directory: Path, *, ttl_seconds: int):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds

    def _path(self, url: str) -> Path:
        return self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def check(self, url: str) -> None:
        try:
            receipt = json.loads(self._path(url).read_text())
            retry_at = float(receipt["retry_at"])
            now = time.time()
            # Invalid receipts or clock rollback cannot defer collection forever.
            if (receipt["url"] == url and math.isfinite(retry_at)
                    and 0 < retry_at - now <= self.ttl_seconds):
                raise SourceRequestDeferred(url, retry_at=retry_at, reason=receipt["reason"])
        except SourceRequestDeferred:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            return

    def record(self, url: str, *, reason: str) -> None:
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".failure-", dir=self.directory)
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"url": url, "reason": reason,
                           "retry_at": time.time() + self.ttl_seconds}, stream)
            os.replace(temporary, self._path(url))
        except OSError:
            # A local cache failure must not turn a source outage into a new
            # collection failure, or prevent trying the remaining mirror.
            pass
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def clear(self, url: str) -> None:
        try:
            self._path(url).unlink(missing_ok=True)
        except OSError:
            pass
