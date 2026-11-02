"""Stable, order-preserving public research shards and cache-safe retirement."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from src.generation.site_data_writer import _write_if_changed

SHARD_RETENTION_SECONDS = 48 * 60 * 60
SHARD_TARGET_BYTES = 1024 * 1024

# Pages rejects any one asset over 25 MiB on every plan. Keep each browser
# file at or below 8 MiB so the research index can grow without a deploy failure.
PUBLIC_RESEARCH_SHARD_MAX_BYTES = 8 * 1024 * 1024
_SHARD_SKIP_KEYS = frozenset({"schema_version", "last_updated"})


def _compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _chunk_records(records: list[Any], max_bytes: int, *, stable: bool = False) -> list[list[Any]]:
    """Pack records so each compact ``{"records": [...]}`` file fits the budget."""

    chunks: list[list[Any]] = []
    current: list[Any] = []
    envelope_bytes = len(_compact_json_bytes({"records": []}))
    current_bytes = envelope_bytes
    for record in records:
        encoded = _compact_json_bytes(record)
        record_bytes = len(encoded)
        trial_bytes = current_bytes + record_bytes + (1 if current else 0)
        if current and trial_bytes > max_bytes:
            chunks.append(current)
            current = [record]
            current_bytes = envelope_bytes + record_bytes
        else:
            current.append(record)
            current_bytes = trial_bytes
        if stable:
            # Anchors depend on the record, not its offset or neighbouring
            # records. Insertions/deletions resynchronize at the next anchor.
            identity = None
            if isinstance(record, dict):
                identity = next(
                    (record.get(key) for key in ("article_id", "slug", "id") if record.get(key)),
                    None,
                )
            anchor = hashlib.sha256(str(identity).encode() if identity is not None else encoded).digest()
            if int.from_bytes(anchor[:8], "big") % SHARD_TARGET_BYTES < min(record_bytes, SHARD_TARGET_BYTES):
                chunks.append(current)
                current = []
                current_bytes = envelope_bytes
    if current:
        chunks.append(current)
    return chunks


def _write_content_shard(key: str, payload: Any, directory: Path) -> str:
    body = _compact_json_bytes(payload)
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", key)[:64]
    name = f"{prefix}-{hashlib.sha256(body).hexdigest()}.json"
    _write_if_changed(directory / name, body)
    return name


def _referenced_shards(value: Any) -> set[str]:
    names = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "file" and isinstance(child, str) and child.startswith("shards/"):
                name = child.removeprefix("shards/")
                if Path(name).name == name:
                    names.add(name)
            names.update(_referenced_shards(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_referenced_shards(child))
    return names


def _refresh_previous_references(directory: Path, now: float) -> None:
    # Mark the last instant at which the previous manifests referenced a shard.
    # A subsequent run no longer touches retired shards, allowing bounded GC.
    for name in ("index.json", "catalogue.json"):
        try:
            previous = json.loads((directory / name).read_text())
        except (OSError, ValueError):
            continue
        for shard in _referenced_shards(previous):
            path = directory / "shards" / shard
            if path.is_file():
                os.utime(path, (now, now))


def _store_shard_value(
    key: str,
    value: Any,
    shard_dir: Path,
    max_bytes: int,
    written: set[str],
) -> dict[str, Any]:
    if isinstance(value, list):
        chunks = _chunk_records(value, max_bytes, stable=True)
        manifest: list[dict[str, Any]] = []
        for chunk in chunks:
            name = _write_content_shard(key, {"records": chunk}, shard_dir)
            written.add(name)
            manifest.append({"file": f"shards/{name}", "count": len(chunk)})
        return {
            "sharded": True,
            "kind": "list",
            "count": len(value),
            "chunks": manifest,
        }

    payload = {"value": value}
    if len(_compact_json_bytes(payload)) <= max_bytes or not isinstance(value, dict):
        name = _write_content_shard(key, payload, shard_dir)
        written.add(name)
        return {"sharded": True, "kind": "value", "file": f"shards/{name}"}

    parts = {
        child_key: _store_shard_value(
            f"{key}-{child_key}",
            child,
            shard_dir,
            max_bytes,
            written,
        )
        for child_key, child in value.items()
    }
    return {"sharded": True, "kind": "object", "parts": parts}


def _largest_inline_key(document: dict[str, Any], sizes: dict[str, int]) -> str | None:
    best_key = None
    best_size = -1
    for key, value in document.items():
        if key in _SHARD_SKIP_KEYS or not isinstance(value, (list, dict)):
            continue
        if isinstance(value, dict) and value.get("sharded") is True:
            continue
        size = sizes[key]
        if size > best_size:
            best_key = key
            best_size = size
    return best_key


def publish_browser_research_index(
    document: dict[str, Any],
    directory: Path,
    *,
    max_bytes: int = PUBLIC_RESEARCH_SHARD_MAX_BYTES,
    now: float | None = None,
) -> dict[str, Any]:
    """Write the Pages copy of the research index as files within ``max_bytes``.

    Astro keeps reading the single build-time ``index.json``. The browser copy
    under ``public/site-data/research`` is uploaded as static assets, so the
    large lists move into ``shards/`` and the index keeps a manifest.
    """

    if max_bytes < 128:
        raise ValueError("max_bytes must leave room for a JSON envelope")
    directory.mkdir(parents=True, exist_ok=True)
    shard_dir = directory / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    published_at = time.time() if now is None else now
    _refresh_previous_references(directory, published_at)
    published = dict(document)
    written: set[str] = set()
    sizes = {key: len(_compact_json_bytes(value)) for key, value in published.items()}
    envelope = 2 + max(0, len(published) - 1) + sum(len(_compact_json_bytes(key)) + 1 for key in published)
    while envelope + sum(sizes.values()) > max_bytes:
        key = _largest_inline_key(published, sizes)
        if key is None:
            break
        before = sizes[key]
        published[key] = _store_shard_value(key, published[key], shard_dir, max_bytes, written)
        sizes[key] = len(_compact_json_bytes(published[key]))
        if sizes[key] >= before:
            break

    articles = published.get("articles")
    catalogue = {
        "schema_version": published.get("schema_version"),
        "last_updated": published.get("last_updated"),
        "articles": articles,
    }
    if isinstance(articles, list) and len(_compact_json_bytes(catalogue)) > max_bytes:
        published["articles"] = _store_shard_value("articles", articles, shard_dir, max_bytes, written)
        catalogue["articles"] = published["articles"]

    roots = {"index.json": _compact_json_bytes(published), "catalogue.json": _compact_json_bytes(catalogue)}
    oversized = [name for name, body in roots.items() if len(body) > max_bytes]
    oversized.extend(name for name in written if (shard_dir / name).stat().st_size > max_bytes)
    if oversized:
        raise RuntimeError(f"Research browser files exceed {max_bytes} bytes: {', '.join(oversized)}")
    for name, body in roots.items():
        _write_if_changed(directory / name, body)
    for path in shard_dir.glob("*.json"):
        if path.name in written:
            os.utime(path, (published_at, published_at))
        elif path.stat().st_mtime < published_at - SHARD_RETENTION_SECONDS:
            path.unlink()
    return published
