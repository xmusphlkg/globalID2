"""Content-addressed Papers generations with reusable immutable objects.

This module prepares and publishes artifacts only. Switching the public
Research Radar pointer remains a separate conditional operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from scripts.automation.dispatch_research_digest import _https_origin
from src.core import get_logger
from .papers_bundle import _object_key_for_papers
from .papers_immutable import ImmutablePapersCandidate, ImmutablePapersFile, _source
from .papers_r2 import R2S3Error, _content_type_for

PREFIX = "releases/sha256"
logger = get_logger(__name__)


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class PapersContentCandidate:
    root: Path
    origin: str
    release_id: str
    generation: str
    entries: tuple[dict[str, Any], ...]
    shards: tuple[tuple[str, bytes], ...]
    manifest: bytes

    @property
    def manifest_key(self) -> str:
        return f"{PREFIX}/catalogues/{self.generation}.json"


def prepare_papers_content(papers: ImmutablePapersCandidate, *, release_id: str) -> PapersContentCandidate:
    """Create a deterministic catalogue without copying unchanged objects."""
    if not isinstance(papers, ImmutablePapersCandidate) or not release_id:
        raise ValueError("Invalid Papers candidate")
    return _prepare(papers.root, papers.origin, papers.files, release_id=release_id)


def prepare_papers_content_from_manifest(root: Path, manifest: dict[str, Any], *,
                                         papers_origin: str, release_id: str) -> PapersContentCandidate:
    """Use the build's digest receipts; do not reread every generated file."""
    origin = _https_origin(papers_origin, "papers_origin")
    if (not release_id or not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("papers_origin") != origin or not isinstance(manifest.get("files"), list)):
        raise ValueError("Invalid Papers build manifest")
    items = []
    seen = set()
    for entry in manifest["files"]:
        path, key = entry["path"], entry["key"]
        if (not isinstance(path, str) or path.startswith("/") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or not isinstance(key, str) or key in seen or _object_key_for_papers(path) != key
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                or type(entry.get("bytes")) is not int or entry["bytes"] < 0):
            raise ValueError("Invalid Papers artifact path")
        seen.add(key)
        items.append(ImmutablePapersFile(path, key, entry["bytes"], entry["sha256"]))
    items.sort(key=lambda item: item.logical_key)
    logger.info("Research papers catalogue prepared from build receipts: release={} files={}", release_id, len(items))
    return _prepare(Path(root).resolve(), origin, tuple(items), release_id=release_id)


def _prepare(root: Path, origin: str, files: tuple[ImmutablePapersFile, ...], *, release_id: str) -> PapersContentCandidate:
    if not files:
        raise ValueError("Papers publication cannot be empty")
    groups: dict[str, list[dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []
    for item in files:
        content_type = _content_type_for(item.path)
        # Equal bytes with different MIME types must not share R2 metadata.
        kind = _hash(content_type.encode())[:16]
        entry = {"path": item.path, "logical_key": item.logical_key,
                 "key": f"{PREFIX}/objects/{item.sha256}/{kind}",
                 "sha256": item.sha256, "bytes": item.size, "content_type": content_type}
        entries.append(entry)
        groups.setdefault(_hash(item.logical_key.encode())[:2], []).append(entry)
    shards: list[tuple[str, bytes]] = []
    descriptors = {}
    for group, items in sorted(groups.items()):
        body = _json({"schema_version": 1, "items": items})
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Papers catalogue shard exceeds safe size")
        digest = _hash(body)
        key = f"{PREFIX}/catalogue-shards/{digest}.json"
        shards.append((key, body))
        descriptors[group] = {"key": key, "sha256": digest, "bytes": len(body), "count": len(items)}
    basis = {"schema_version": 2, "papers_origin": origin, "release_id": release_id,
             "file_count": len(entries), "shards": descriptors}
    generation = _hash(_json(basis))
    body = _json({**basis, "generation_sha256": generation})
    return PapersContentCandidate(root, origin, release_id, generation, tuple(entries), tuple(shards), body)


def publish_papers_content(candidate: PapersContentCandidate, *, client: Any,
                           previous_objects: set[str] | None = None, workers: int = 16) -> dict[str, Any]:
    """Upload changed objects, then catalogue shards and the immutable root.

    ``previous_objects`` must come from an earlier committed catalogue whose
    content-addressed keys are protected by the bucket's immutable policy.
    Objects are not downloaded again for verification; counters are logged.
    """
    if type(workers) is not int or not 1 <= workers <= 96:
        raise ValueError("Papers upload workers must be 1–96")
    known = previous_objects or set()
    uploaded = 0
    seen: set[str] = set()
    skipped = 0
    started = time.monotonic()
    pending_entries = []
    for entry in candidate.entries:
        key = entry["key"]
        if key in seen:
            continue
        seen.add(key)
        if key in known:
            skipped += 1
            continue
        pending_entries.append(entry)

    def transfer(entry):
        key = entry["key"]
        path = _source(candidate.root, entry["path"])
        body = path.read_bytes()
        try:
            client.put_object_conditional(key, body, previous=None, content_type=entry["content_type"],
                                          cache_control="public, max-age=31536000, immutable")
            return True
        except R2S3Error as error:
            if error.status != 412:
                raise
            return False

    def bounded_map(pool, function, items):
        remaining = iter(items)
        pending = set()
        try:
            while True:
                while len(pending) < workers * 2:
                    item = next(remaining, None)
                    if item is None:
                        break
                    pending.add(pool.submit(function, item))
                if not pending:
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
        finally:
            for future in pending:
                future.cancel()

    def put_metadata(item):
        key, body = item
        try:
            client.put_object_conditional(key, body, previous=None,
                content_type="application/json; charset=utf-8",
                cache_control="public, max-age=31536000, immutable")
        except R2S3Error as error:
            if error.status != 412:
                raise
        return None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research-papers") as pool:
        last_progress = started
        for changed in bounded_map(pool, transfer, pending_entries):
            uploaded += int(changed)
            skipped += int(not changed)
            if time.monotonic() - last_progress >= 15:
                logger.info("Research papers upload progress: completed={} total={} uploaded={} reused={}",
                            uploaded + skipped, len(seen), uploaded, skipped)
                last_progress = time.monotonic()
        # The root must only be committed after all object and shard uploads.
        list(bounded_map(pool, put_metadata, candidate.shards))
        put_metadata((candidate.manifest_key, candidate.manifest))
    logger.info("Research papers objects published: uploaded={} reused={} shards={} elapsed_seconds={:.2f}",
                uploaded, skipped, len(candidate.shards), time.monotonic() - started)
    return {"generation_sha256": candidate.generation,
            "manifest_url": candidate.origin + "/" + candidate.manifest_key,
            "manifest_sha256": _hash(candidate.manifest),
            "uploaded_objects": uploaded, "reused_objects": skipped}
