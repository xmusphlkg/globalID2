"""Verified CDC archive pages shared by retries and subdivision collections."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlencode

from src.core import get_logger

logger = get_logger(__name__)
CACHE_DIR = Path("data/cache/us/nndss_archive")
REVALIDATE_SECONDS = 30 * 24 * 60 * 60


def _atomic_write(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_page(directory, source_url, *, now):
    try:
        receipt = json.loads((directory / "receipt.json").read_bytes())
        payload = (directory / "page.json").read_bytes()
        if (receipt["source_url"] == source_url
                and 0 <= now - receipt["fetched_at"] < REVALIDATE_SECONDS
                and receipt["sha256"] == hashlib.sha256(payload).hexdigest()):
            return payload
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _download(crawler, url, params):
    response = crawler.get(url, params=params)
    payload = response.content
    rows = json.loads(payload)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("CDC archive response must be a JSON array of records")
    return payload


def fetch_archive_page(crawler, resource, *, offset, page_size, force=False):
    """Refresh discovery upstream; reuse only pages of the same source revision.

    The per-page receipt is written after a complete validated response, so a
    failure on a later page can resume without downloading earlier pages.
    Missing revision metadata always requires a fresh download. Cache hits
    still become independent durable artifacts in the requesting task.
    """
    url = f"https://data.cdc.gov/resource/{resource['id']}.json"
    params = {"$limit": page_size, "$offset": offset}
    source_url = url + "?" + urlencode(params)
    revision = {key: resource.get(key) for key in (
        "updatedAt", "rowsUpdatedAt", "data_updated_at", "metadata_updated_at",
    ) if resource.get(key)}
    if not revision:
        return _download(crawler, url, params), source_url, False
    identity = {"version": 1, "url": source_url, "revision": revision,
                "columns": resource.get("columns_field_name")}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = CACHE_DIR / key
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = None if force else _read_page(directory, source_url, now=time.time())
        if payload is not None:
            return payload, source_url, True
        payload = _download(crawler, url, params)
        receipt = {"source_url": source_url, "fetched_at": time.time(),
                   "sha256": hashlib.sha256(payload).hexdigest()}
        try:
            _atomic_write(directory / "page.json", payload)
            _atomic_write(directory / "receipt.json", json.dumps(receipt).encode())
        except OSError as exc:
            logger.warning("US archive cache write failed | url={} error={}", source_url, exc)
        return payload, source_url, False
