"""Content-addressed Papers generations with reusable immutable objects.

This module prepares and publishes artifacts only. Switching the public
Research Radar pointer remains a separate conditional operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import gzip
import json
from pathlib import Path
import re
import time
from typing import Any

from scripts.automation.dispatch_research_digest import _https_origin
from src.core import get_logger
from .papers_bundle import _object_key_for_papers, is_obsolete_research_build_path
from .papers_immutable import ImmutablePapersCandidate, ImmutablePapersFile, _source
from .papers_r2 import R2S3Error, _content_type_for

PREFIX = "releases/sha256"
logger = get_logger(__name__)


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _source_objects(candidate):
    objects = [(entry, candidate.root) for entry in candidate.entries if 'paper_template' not in entry]
    objects.extend((entry, candidate.template_root) for entry in candidate.template_objects)
    seen = set()
    for entry, root in objects:
        if entry['key'] not in seen:
            seen.add(entry['key'])
            yield entry, root


def _wire_body(body: bytes, content_type: str) -> tuple[bytes, dict]:
    if len(body) >= 1024 and content_type.startswith((
            'text/', 'application/json', 'application/xml', 'image/svg+xml')):
        encoded = gzip.compress(body, compresslevel=6, mtime=0)
        if len(encoded) < len(body):
            return encoded, {'content_encoding': 'gzip'}
    return body, {}


@dataclass(frozen=True)
class PapersContentCandidate:
    root: Path
    origin: str
    release_id: str
    generation: str
    entries: tuple[dict[str, Any], ...]
    shards: tuple[tuple[str, bytes], ...]
    manifest: bytes
    template_root: Path | None = None
    template_objects: tuple[dict[str, Any], ...] = ()

    @property
    def object_keys(self) -> set[str]:
        return {item['key'] for item in (*self.entries, *self.template_objects)}

    @property
    def manifest_key(self) -> str:
        return f"{PREFIX}/catalogues/{self.generation}.json"


def verified_existing_objects(candidate: PapersContentCandidate, inventory: dict, *, workers: int = 8) -> set[str]:
    """Prove staged payload identity from wire size and single-PUT MD5 ETag.

    An inventory name alone is never a reuse receipt. Both the original raw
    representation and our deterministic gzip representation are accepted.
    This read-only helper bootstraps reuse during the initial migration.
    """
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError('Inventory verification workers must be 1–32')
    def check(source):
        entry, root = source
        remote = inventory.get(entry['key'])
        if not isinstance(remote, dict):
            return None
        body = _source(root, entry['path']).read_bytes()
        if len(body) != entry['bytes'] or _hash(body) != entry['sha256']:
            raise ValueError(f"Papers source changed during inventory verification: {entry['path']}")
        etag = str(remote.get('etag', '')).strip('"')
        if (remote.get('bytes') == len(body)
                and etag == hashlib.md5(body, usedforsecurity=False).hexdigest()):
            return entry['key']
        wire, _ = _wire_body(body, entry['content_type'])
        if (remote.get('bytes') == len(wire)
                and etag == hashlib.md5(wire, usedforsecurity=False).hexdigest()):
            return entry['key']
        return None
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='papers-verify') as pool:
        return {key for key in pool.map(check, _source_objects(candidate)) if key is not None}


def prepare_papers_content(papers: ImmutablePapersCandidate, *, release_id: str) -> PapersContentCandidate:
    """Create a deterministic catalogue without copying unchanged objects."""
    if not isinstance(papers, ImmutablePapersCandidate) or not release_id:
        raise ValueError("Invalid Papers candidate")
    return _prepare(papers.root, papers.origin, papers.files, release_id=release_id)


def prepare_papers_content_from_manifest(root: Path, manifest: dict[str, Any], *,
                                         papers_origin: str, release_id: str,
                                         template_dir: Path | None = None) -> PapersContentCandidate:
    """Use the build's digest receipts; do not reread every generated file."""
    origin = _https_origin(papers_origin, "papers_origin")
    if (not release_id or not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("papers_origin") != origin or not isinstance(manifest.get("files"), list)):
        raise ValueError("Invalid Papers build manifest")
    items = []
    seen = set()
    for entry in manifest["files"]:
        path, key = entry["path"], entry["key"]
        if isinstance(path, str) and is_obsolete_research_build_path(path):
            raise ValueError("Obsolete research files in papers bundle; rebuild the bundle")
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
    return _prepare(Path(root).resolve(), origin, tuple(items), release_id=release_id,
                    template_dir=template_dir)


def _prepare(root: Path, origin: str, files: tuple[ImmutablePapersFile, ...], *, release_id: str,
             template_dir: Path | None = None) -> PapersContentCandidate:
    if not files:
        raise ValueError("Papers publication cannot be empty")
    groups: dict[str, list[dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []
    templates, template_objects, template_root = {}, (), None
    if template_dir is not None:
        from .papers_content_templates import prepare_template_descriptors
        templates, template_objects, template_root = prepare_template_descriptors(root, files, template_dir)
    for item in files:
        if is_obsolete_research_build_path(item.path):
            raise ValueError("Obsolete research files in papers bundle; rebuild the bundle")
        content_type = _content_type_for(item.path)
        # Equal bytes with different MIME types must not share R2 metadata.
        kind = _hash(content_type.encode())[:16]
        entry = {"path": item.path, "logical_key": item.logical_key,
                 "key": f"{PREFIX}/objects/{item.sha256}/{kind}",
                 "sha256": item.sha256, "bytes": item.size, "content_type": content_type}
        if item.path in templates:
            descriptor = templates[item.path]
            entry['key'] = f"paper-template-v2/records/{descriptor['record_sha256']}.json"
            entry['paper_template'] = descriptor
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
    return PapersContentCandidate(root, origin, release_id, generation, tuple(entries), tuple(shards), body,
                                  template_root, template_objects)


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
    for entry, source_root in _source_objects(candidate):
        key = entry["key"]
        if key in seen:
            continue
        seen.add(key)
        if key in known:
            skipped += 1
            continue
        pending_entries.append((entry, source_root))

    def transfer(source):
        entry, source_root = source
        key = entry["key"]
        path = _source(source_root, entry["path"])
        body = path.read_bytes()
        if len(body) != entry["bytes"] or _hash(body) != entry["sha256"]:
            raise ValueError(f"Papers source changed during publish: {entry['path']}")
        # Keys and catalogue digests describe decoded content. The reader
        # already decodes Content-Encoding before rewriting HTML/CSS, so new
        # text objects can use fewer wire bytes without changing generations.
        body, metadata = _wire_body(body, entry['content_type'])
        try:
            client.put_object_conditional(key, body, previous=None, content_type=entry["content_type"],
                                          cache_control="public, max-age=31536000, immutable", **metadata)
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
