"""Parse independent bundle HTML on multiple CPU cores before asset copying.

Workers are read-only. Only the parent merges receipts and writes assets or
manifests, so shared routing and bundle state never have concurrent writers.
"""
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .artifact_index import ArtifactIndex

_worker_index: ArtifactIndex | None = None


def _initialize(root: Path):
    global _worker_index
    _worker_index = ArtifactIndex(root)


def _parse(job):
    from .bundle_assets import _asset_references
    path, entry = job
    key = _worker_index.key(path)
    _worker_index.entries[key] = entry
    _asset_references(path, _worker_index)
    return key, _worker_index.entries[key]


def warm_asset_references(pages: list[Path], index: ArtifactIndex, *, workers: int | None = None) -> int:
    from .bundle_assets import REFERENCE_SCHEMA
    workers = int(os.environ.get("GIDS_BUNDLE_ASSET_WORKERS", min(8, os.cpu_count() or 1))) if workers is None else workers
    if not 1 <= workers <= 16:
        raise ValueError("GIDS_BUNDLE_ASSET_WORKERS must be 1–16")
    if workers == 1 or len(pages) < 64:
        return 0
    pending = []
    for path in pages:
        entry = index.entries.get(index.key(path), {})
        parsed = entry.get("asset_references", {})
        digests = index.cached(path)
        if (isinstance(parsed, dict) and parsed.get("schema") == REFERENCE_SCHEMA and digests is not None
                and parsed.get("sha256") == digests["sha256"] and isinstance(parsed.get("tokens"), list)
                and all(isinstance(token, str) for token in parsed["tokens"])):
            continue
        pending.append((path, entry))
    if len(pending) < 64:
        return 0
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
        initializer=_initialize, initargs=(index.root,))
    try:
        for key, entry in pool.map(_parse, pending, chunksize=32):
            index.entries[key] = entry
    finally:
        # Drain running readers and cancel queued work on failure before returning.
        pool.shutdown(wait=True, cancel_futures=True)
    print(f"Bundle assets: parsed {len(pending)} HTML reference lists with {workers} processes", flush=True)
    return len(pending)
