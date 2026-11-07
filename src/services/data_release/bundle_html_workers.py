"""Rewrite independent changed pages on CPU workers; keep one output writer."""
from __future__ import annotations

import hashlib
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor


def _rewrite(job):
    source, target, expected, kind, origins = job
    body = source.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected:
        raise ValueError(f"Build artifact changed during partitioning: {source}")
    if kind == "papers":
        from .papers_bundle import rewrite_papers_html as rewrite
    elif kind == "reports":
        from .reports_bundle import rewrite_reports_html as rewrite
    else:
        raise ValueError(f"Unknown partition: {kind}")
    return source, target, rewrite(body.decode("utf-8"), **origins).encode("utf-8")


def rewrite_changed_pages(pending, index, *, kind, origins, workers=None):
    if not pending:
        return
    workers = int(os.environ.get("GIDS_BUNDLE_HTML_WORKERS", min(8, os.cpu_count() or 1))) if workers is None else workers
    if not 1 <= workers <= 16:
        raise ValueError("GIDS_BUNDLE_HTML_WORKERS must be 1–16")
    jobs = [(source, target, expected, kind, origins) for source, target, expected in pending]
    def consume(results):
        for source, target, body in results:
            index.write(target, body)
            source.unlink()
    if workers == 1 or len(jobs) < 32:
        consume(map(_rewrite, jobs))
    else:
        pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
        try:
            # Bound pending decoded HTML even for a full template migration.
            for offset in range(0, len(jobs), 128):
                consume(pool.map(_rewrite, jobs[offset:offset + 128], chunksize=4))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    print(f"Bundle HTML: rewrote {len(jobs)} changed {kind} pages with {workers if len(jobs) >= 32 else 1} processes", flush=True)
