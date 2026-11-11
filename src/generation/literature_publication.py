"""Freeze a private Research Radar JSON export into immutable public objects.

This module operates on a completed, private export. It never reads the live
database and never publishes a remote pointer; the caller owns snapshot
selection and the remote compare-and-swap publication transaction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from src.generation.site_data_literature import (
    attach_surveillance_evidence,
    collect_literature_export,
    write_literature_artifacts,
)
from src.core import get_logger
from src.services.data_release.artifact_index import ArtifactIndex
from src.services.data_release.surveillance_publish import validate_manifest

logger = get_logger(__name__)


def _referenced_shards(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "file" and isinstance(child, str) and child.startswith("shards/"):
                name = child.removeprefix("shards/")
                if not name or Path(name).name != name or not name.endswith(".json"):
                    raise ValueError("Invalid literature shard reference")
                references.add(name)
            references.update(_referenced_shards(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_referenced_shards(child))
    return references


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".literature-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def select_index_artifacts(source: Path) -> list[str]:
    """Select the current browser index and its shards, excluding retired files.

    Other projections must be selected explicitly by their own publisher; a
    list of index files is not a complete research site or article bundle.
    """
    source = Path(source)
    names = {"index.json", "catalogue.json"}
    for name in tuple(names):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or unsafe literature index: {name}")
        names.update(f"shards/{shard}" for shard in _referenced_shards(json.loads(path.read_bytes())))
    return sorted(names)


async def export_literature_snapshot(session, *, diseases_by_id: dict, surveillance_coverage: dict,
                                     situation_snapshot: dict | None, private_dir: Path,
                                     limit: int | None, title_cache_path: Path | None = None,
                                     related_cache_path: Path | None = None) -> dict:
    """Generate only Research Radar inputs from a caller-owned read snapshot.

    The caller must hold a read-only repeatable-read database transaction and
    supply the independently published surveillance and Situation inputs. The
    private directory must belong exclusively to this candidate: no generated
    source or public website directory is modified here.
    """
    private_dir = Path(private_dir)
    if private_dir.is_symlink() or (private_dir.exists() and any(private_dir.iterdir())):
        raise ValueError("Literature snapshot requires an empty private directory")
    private_dir.mkdir(parents=True, exist_ok=True)
    payload = await collect_literature_export(
        session, diseases_by_id=diseases_by_id, surveillance_coverage=surveillance_coverage,
        limit=limit, title_cache_path=title_cache_path,
    )
    payload = attach_surveillance_evidence(payload, situation_snapshot, diseases_by_id=diseases_by_id,
                                           related_cache_path=related_cache_path)
    logger.info("Research candidate projected: articles={} preprints={} bibliographic={}",
                len(payload.get("articles") or []), len(payload.get("preprints") or []),
                len(payload.get("bibliographic_articles") or []))
    article_digests: dict[str, str] = {}
    write_literature_artifacts(payload, private_dir / "build", private_dir / "public", article_digests=article_digests)
    _atomic_write(private_dir / "article-inputs.json", json.dumps({"schema": 1, "articles": article_digests},
                  sort_keys=True, separators=(",", ":")).encode())
    return payload


def freeze_literature_json(source: Path, destination: Path, *, release_id: str,
                           generated_at: str, logical_names: list[str] | None = None) -> dict:
    """Freeze a complete set of JSON files without exposing a partial pointer.

    Logical paths remain in the manifest so clients can pin requests to one
    generation. Unchanged files reuse local digest receipts and existing
    content-addressed objects without a second read.
    """
    source, destination = Path(source), Path(destination)
    if not release_id or not generated_at or not source.is_dir() or source.is_symlink():
        raise ValueError("Literature publication requires a source and release identity")
    if source.resolve() == destination.resolve() or destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Publication output must be outside the source tree")
    if logical_names is None:
        paths = sorted(source.rglob("*"))
    else:
        if len(set(logical_names)) != len(logical_names):
            raise ValueError("Duplicate literature publication paths")
        paths = []
        for name in sorted(logical_names):
            if not isinstance(name, str) or not name or Path(name).is_absolute():
                raise ValueError("Invalid literature publication path")
            relative = Path(name)
            if any(part in {".", ".."} for part in relative.parts) or relative.as_posix() != name:
                raise ValueError("Invalid literature publication path")
            path = source / relative
            if not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError(f"Missing or unsafe literature artifact: {name}")
            # A symlink in any ancestor can escape the private snapshot or be
            # retargeted while a generation is being frozen.
            if any(parent.is_symlink() for parent in (path, *path.parents) if parent != source.parent):
                raise ValueError("Literature publication does not accept symlinks")
            paths.append(path)
    if any(path.is_symlink() for path in paths):
        raise ValueError("Literature publication does not accept symlinks")
    started = time.monotonic()
    index = ArtifactIndex(source)
    files = {}
    referenced_shards: set[str] = set()
    created = 0
    for path in paths:
        if not path.is_file():
            continue
        name = path.relative_to(source).as_posix()
        if not name.endswith(".json"):
            raise ValueError(f"Unexpected literature artifact: {name}")
        if name in {"index.json", "catalogue.json"}:
            payload = json.loads(path.read_bytes())
            if not isinstance(payload, dict):
                raise ValueError(f"Literature index must be an object: {name}")
            referenced_shards.update(_referenced_shards(payload))
        # Shard writers already include the SHA-256 of the exact serialized
        # bytes in their names. Reuse that production receipt instead of
        # reopening and hashing every shard on each candidate.
        shard_digest = re.fullmatch(r"shards/[a-z0-9_-]+-([a-f0-9]{64})\.json", name)
        info = ({"sha256": shard_digest[1], "bytes": path.stat().st_size}
                if shard_digest else index.describe(path))
        digest = info["sha256"]
        relative = f"objects/{digest}.json"
        target = destination / relative
        if target.is_symlink():
            raise ValueError("Unsafe literature object")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".literature-object-", dir=target.parent)
            os.close(descriptor)
            try:
                shutil.copyfile(path, temporary)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            created += 1
        files[name] = {"url": relative, "sha256": digest, "bytes": info["bytes"]}
    if "index.json" not in files:
        raise ValueError("Literature publication requires index.json")
    missing_shards = {f"shards/{name}" for name in referenced_shards} - files.keys()
    if missing_shards:
        raise ValueError(f"Literature publication is missing {len(missing_shards)} referenced shards")
    manifest = {"schema_version": 1, "release_id": release_id,
                "generated_at": generated_at, "files": files}
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()
    validate_manifest(body)
    _atomic_write(destination / "current.json", body)
    index.save()
    logger.info("Research candidate frozen: files={} new_objects={} source_reads={} bytes_read={} seconds={:.2f}",
                len(files), created, index.read_files, index.read_bytes, time.monotonic() - started)
    return manifest
