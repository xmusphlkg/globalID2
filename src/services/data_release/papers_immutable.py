"""Experimental immutable Papers generations. Not connected to publication.

The caller must supply the exact local split manifest, an R2 client with atomic
conditional writes, and a public read adapter. No Pages deployment, pointer
change, deletion or mail is performed here. Content-addressed keys prevent
collisions among cooperating publishers; bucket-wide write restrictions remain
necessary to prevent another actor from overwriting or deleting those objects.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from scripts.automation.dispatch_research_digest import _https_origin
from .papers_bundle import _object_key_for_papers
from .papers_r2 import _content_type_for

MAX_FILE = 100 * 1024 * 1024
MAX_FILES = 100000
MAX_MANIFEST = 32 * 1024 * 1024
HEX = re.compile(r"^[a-f0-9]{64}$")
PREFIX = "releases/sha256/"


class ImmutablePapersError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _key(value: Any) -> str:
    if (not isinstance(value, str) or not value or value.startswith("/") or
            any(part in {"", ".", ".."} for part in value.rstrip("/").split("/")) or
            any(char in value for char in ("\\", "?", "#", "%", "\0"))):
        raise ImmutablePapersError("immutable_papers_invalid_key")
    return value


def _source(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ImmutablePapersError("immutable_papers_invalid_path")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ImmutablePapersError("immutable_papers_symlink_forbidden")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ImmutablePapersError("immutable_papers_file_missing")
    return current


@dataclass(frozen=True)
class ImmutablePapersFile:
    path: str
    logical_key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ImmutablePapersCandidate:
    root: Path
    origin: str
    generation: str
    files: tuple[ImmutablePapersFile, ...]
    manifest: bytes

    @property
    def prefix(self) -> str:
        return PREFIX + self.generation + "/"

    @property
    def manifest_key(self) -> str:
        return self.prefix + "papers-manifest.json"

    @property
    def manifest_url(self) -> str:
        return self.origin + "/" + self.manifest_key


def _candidate_identity(origin: str, files: tuple[ImmutablePapersFile, ...]) -> tuple[str, bytes]:
    if (not files or len(files) > MAX_FILES or
            list(files) != sorted(files, key=lambda item: item.logical_key) or
            len({item.path for item in files}) != len(files) or
            len({item.logical_key for item in files}) != len(files)):
        raise ImmutablePapersError("immutable_papers_candidate_invalid")
    for item in files:
        if (not isinstance(item, ImmutablePapersFile) or _key(item.logical_key) != item.logical_key or
                _object_key_for_papers(item.path) != item.logical_key or item.logical_key == "papers-manifest.json" or
                type(item.size) is not int or not 0 <= item.size <= MAX_FILE or not HEX.fullmatch(item.sha256)):
            raise ImmutablePapersError("immutable_papers_candidate_invalid")
    logical = [{"path": item.path, "logical_key": item.logical_key, "bytes": item.size,
                "sha256": item.sha256} for item in files]
    generation = _sha(_json({"schema_version": 1, "papers_origin": origin, "files": logical}))
    prefix = PREFIX + generation + "/"
    public_files = [{"path": item.path, "key": prefix + item.logical_key,
                     "url": origin + "/" + prefix + item.logical_key,
                     "bytes": item.size, "sha256": item.sha256} for item in files]
    body = _json({"schema_version": 1, "papers_origin": origin, "generation_sha256": generation,
                  "file_count": len(public_files), "files": public_files})
    if len(body) > MAX_MANIFEST:
        raise ImmutablePapersError("immutable_papers_manifest_too_large")
    return generation, body


def prepare_immutable_papers(*, papers_dir: Path, manifest: Mapping[str, Any],
                             papers_origin: str) -> ImmutablePapersCandidate:
    """Freeze candidate identity from verified bytes, with no external effects."""
    try:
        origin = _https_origin(papers_origin, "papers_origin")
    except Exception as exc:
        raise ImmutablePapersError("immutable_papers_invalid_origin") from exc
    if (manifest.get("schema_version") != 1 or manifest.get("papers_origin") != origin or
            not isinstance(manifest.get("files"), list) or not 1 <= len(manifest["files"]) <= MAX_FILES):
        raise ImmutablePapersError("immutable_papers_manifest_invalid")
    files = []
    paths, keys = set(), set()
    root = papers_dir.resolve()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ImmutablePapersError("immutable_papers_manifest_invalid")
        path, key = entry.get("path"), _key(entry.get("key"))
        if not isinstance(path, str) or path.endswith("/") or key == "papers-manifest.json":
            raise ImmutablePapersError("immutable_papers_invalid_path")
        if _object_key_for_papers(path) != key or entry.get("url") != origin + "/" + key:
            raise ImmutablePapersError("immutable_papers_manifest_invalid")
        if path in paths or key in keys:
            raise ImmutablePapersError("immutable_papers_duplicate_file")
        paths.add(path)
        keys.add(key)
        source = _source(root, path)
        size, expected = entry.get("bytes"), entry.get("sha256")
        if type(size) is not int or not 0 <= size <= MAX_FILE or not isinstance(expected, str) or not HEX.fullmatch(expected):
            raise ImmutablePapersError("immutable_papers_manifest_invalid")
        if source.stat().st_size != size or _sha(source.read_bytes()) != expected:
            raise ImmutablePapersError("immutable_papers_source_changed")
        files.append(ImmutablePapersFile(path, key, size, expected))
    files.sort(key=lambda item: item.logical_key)
    # Domain and schema are part of the generation identity. A release on
    # another host must not silently reuse a generation/binding.
    frozen = tuple(files)
    generation, body = _candidate_identity(origin, frozen)
    return ImmutablePapersCandidate(root, origin, generation, frozen, body)


def _put_once(client: Any, key: str, body: bytes, content_type: str) -> None:
    """Retry-safe conditional create. Never overwrite an existing version."""
    try:
        client.put_object_conditional(key, body, previous=None, content_type=content_type,
                                      cache_control="public, max-age=31536000, immutable")
    except Exception as exc:
        # An interrupted response or a concurrent identical create can leave
        # the exact intended bytes committed. Any different result fails.
        try:
            existing = client.get_object(key)
        except Exception:
            raise ImmutablePapersError("immutable_papers_write_unconfirmed") from exc
        if existing != body:
            raise ImmutablePapersError("immutable_papers_write_conflict") from exc
    if client.get_object(key) != body:
        raise ImmutablePapersError("immutable_papers_readback_mismatch")


def publish_immutable_papers(*, candidate: ImmutablePapersCandidate, client: Any,
                             public_read: Callable[[str, int], bytes]) -> dict[str, str]:
    """Upload and verify a generation, returning a candidate Pages binding.

    The manifest is committed last, after every object has been conditionally
    created and read back through R2 and its public origin. No mutable pointer
    is touched. A failure leaves harmless orphaned content-addressed objects;
    re-running with the same candidate verifies and resumes them.
    """
    if not callable(getattr(client, "put_object_conditional", None)) or not callable(public_read):
        raise ImmutablePapersError("immutable_papers_safe_transport_required")
    try:
        if _https_origin(candidate.origin, "papers_origin") != candidate.origin:
            raise ValueError("noncanonical origin")
        generation, manifest = _candidate_identity(candidate.origin, candidate.files)
        if generation != candidate.generation or manifest != candidate.manifest:
            raise ValueError("candidate identity mismatch")
    except Exception as exc:
        raise ImmutablePapersError("immutable_papers_candidate_invalid") from exc
    for item in candidate.files:
        path = _source(candidate.root, item.path)
        body = path.read_bytes()
        if len(body) != item.size or _sha(body) != item.sha256:
            raise ImmutablePapersError("immutable_papers_source_changed")
        key = candidate.prefix + item.logical_key
        _put_once(client, key, body, _content_type_for(item.path))
        if public_read(candidate.origin + "/" + key, item.size + 1) != body:
            raise ImmutablePapersError("immutable_papers_public_object_mismatch")
    _put_once(client, candidate.manifest_key, candidate.manifest, "application/json; charset=utf-8")
    if public_read(candidate.manifest_url, len(candidate.manifest) + 1) != candidate.manifest:
        raise ImmutablePapersError("immutable_papers_public_manifest_mismatch")
    return {
        "papers_origin": candidate.origin,
        "papers_manifest_url": candidate.manifest_url,
        "papers_manifest_sha256": _sha(candidate.manifest),
        "papers_generation_sha256": candidate.generation,
    }


def bind_pages_release(identity: Mapping[str, Any], binding: Mapping[str, str]) -> dict[str, Any]:
    """Return a proposed binding; persist it atomically with recovery metadata."""
    fields = ("release_id", "build_digest", "source_commit", "deployment_branch")
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in fields):
        raise ImmutablePapersError("immutable_pages_identity_required")
    if not HEX.fullmatch(identity["build_digest"]):
        raise ImmutablePapersError("immutable_pages_identity_invalid")
    origin, generation = binding.get("papers_origin", ""), binding.get("papers_generation_sha256", "")
    try:
        if _https_origin(origin, "papers_origin") != origin:
            raise ValueError("noncanonical origin")
    except Exception as exc:
        raise ImmutablePapersError("immutable_papers_binding_invalid") from exc
    if (not HEX.fullmatch(generation) or not HEX.fullmatch(binding.get("papers_manifest_sha256", "")) or
            binding.get("papers_manifest_url") != origin + "/" + PREFIX + generation + "/papers-manifest.json"):
        raise ImmutablePapersError("immutable_papers_binding_invalid")
    # This is preparation only. Existing release files and recovery checkpoints
    # must be upgraded together before a Pages deployment uses the result.
    return {**identity, **{key: binding[key] for key in (
        "papers_origin", "papers_generation_sha256", "papers_manifest_url", "papers_manifest_sha256")}}
