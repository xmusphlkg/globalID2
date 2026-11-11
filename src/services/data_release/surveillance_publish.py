"""Publish immutable browser JSON first and atomically switch its version pointer.

This channel owns only its R2 prefix. It does not render HTML, list the Papers
bucket, delete historical objects, or depend on a Pages deployment.
"""
from __future__ import annotations

import fcntl
import base64
from contextlib import contextmanager
import hashlib
import gzip
import json
import re
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .artifact_index import ArtifactIndex
from .papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from src.core import get_logger

logger = get_logger(__name__)

DEFAULT_PREFIX = "site-data/surveillance"
OBJECT = re.compile(r"objects/([a-f0-9]{64})\.json\Z")
LOGICAL = re.compile(r"(?:[a-z0-9_-]+/)*[a-z0-9_.-]+\.json\Z", re.IGNORECASE)
STARTING_BASELINE = "starting-baseline.json"


def validate_manifest(body: bytes) -> tuple[dict, dict[str, dict]]:
    value = json.loads(body)
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or not isinstance(value.get("release_id"), str) or not value["release_id"]
            or not isinstance(value.get("files"), dict) or not value["files"]):
        raise ValueError("Invalid surveillance version manifest")
    objects = {}
    for logical, entry in value["files"].items():
        if (not isinstance(logical, str) or not LOGICAL.fullmatch(logical) or ".." in logical.split("/")
                or not isinstance(entry, dict)):
            raise ValueError("Invalid surveillance logical path")
        url = entry.get("url", "")
        match = OBJECT.fullmatch(url) if isinstance(url, str) else None
        if (not match or match[1] != entry.get("sha256") or type(entry.get("bytes")) is not int
                or entry["bytes"] < 0):
            raise ValueError(f"Invalid immutable surveillance object: {logical}")
        if url in objects and objects[url] != {"sha256": entry["sha256"], "bytes": entry["bytes"]}:
            raise ValueError("Conflicting surveillance object descriptors")
        objects[url] = {"sha256": entry["sha256"], "bytes": entry["bytes"]}
    return value, objects


def _prefix(value: str) -> str:
    value = value.strip("/")
    if not value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Invalid surveillance R2 prefix")
    return value


def _target(config: R2PapersConfig, prefix: str) -> str:
    # Bind the receipt to a destination without storing credentials or URLs.
    return hashlib.sha256(f"{config.s3_endpoint}/{config.bucket}/{prefix}".encode()).hexdigest()


@contextmanager
def _publication_lock(directory: Path, config: R2PapersConfig, prefix: str):
    lock_dir = directory.parent / ".surveillance-publish-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / _target(config, prefix)).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _starting_baseline(directory: Path, config: R2PapersConfig, prefix: str) -> tuple[bool, bytes | None]:
    path = directory / ".publication" / STARTING_BASELINE
    if not path.exists() and not path.is_symlink():
        return False, None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
            raise ValueError("Invalid baseline receipt file")
        receipt = json.loads(path.read_bytes())
        if (not isinstance(receipt, dict) or type(receipt.get("schema")) is not int or receipt["schema"] != 1
                or receipt.get("prefix") != prefix or receipt.get("target_sha256") != _target(config, prefix)):
            raise ValueError("Baseline receipt destination changed")
        encoded = receipt["previous_b64"]
        previous = None if encoded is None else base64.b64decode(encoded, validate=True)
        sha = hashlib.sha256(previous).hexdigest() if previous is not None else None
        if receipt["baseline_sha256"] != sha:
            raise ValueError("Baseline receipt checksum mismatch")
        if previous is not None:
            validate_manifest(previous)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError("Captured publication starting baseline is damaged or belongs to another destination") from error
    return True, previous


def _atomic_receipt(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".baseline-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def capture_surveillance_baseline(directory: Path, config: R2PapersConfig, *,
                                  prefix: str = DEFAULT_PREFIX, client=None) -> dict:
    """Capture the public pointer before reading data; never refresh a receipt.

    No local current.json or generated objects are required. GET errors remain
    errors: only the client's actual not-found result can capture a null base.
    """
    directory, prefix = Path(directory), _prefix(prefix)
    with _publication_lock(directory, config, prefix):
        exists, previous = _starting_baseline(directory, config, prefix)
        if not exists:
            active = client or R2S3Client(config)
            try:
                previous = active.get_object(f"{prefix}/current.json")
            finally:
                if client is None:
                    active.close()
            if previous is not None:
                if len(previous) > 2 * 1024 * 1024:
                    raise ValueError("Public data manifest exceeds supported size")
                validate_manifest(previous)
            _atomic_receipt(directory / ".publication" / STARTING_BASELINE, {
                "schema": 1, "prefix": prefix, "target_sha256": _target(config, prefix),
                "baseline_sha256": hashlib.sha256(previous).hexdigest() if previous is not None else None,
                "previous_b64": base64.b64encode(previous).decode("ascii") if previous is not None else None,
            })
        return {"baseline_captured": True, "reused": exists, "prefix": prefix,
            "pointer_present": previous is not None,
            "baseline_sha256": hashlib.sha256(previous).hexdigest() if previous is not None else None}


def publish_surveillance_data(
    directory: Path, config: R2PapersConfig, *, prefix: str = DEFAULT_PREFIX,
    workers: int = 4, client=None, objects_dir: Path | None = None,
    require_starting_baseline: bool = False,
    trust_frozen_objects: bool = False,
) -> dict:
    if not 1 <= workers <= 8:
        raise ValueError("Surveillance upload workers must be 1–8")
    started = time.monotonic()
    directory, prefix = Path(directory), _prefix(prefix)
    if trust_frozen_objects and (prefix != "site-data/research-publication" or not require_starting_baseline):
        raise ValueError("Trusted frozen objects require a fenced research publication")
    body = (directory / "current.json").read_bytes()
    manifest, objects = validate_manifest(body)
    objects_root = objects_dir or directory / "objects"
    index = ArtifactIndex(objects_root, cache_dir=objects_root.parent / ".surveillance-publish-index") if not trust_frozen_objects else None
    # Validate every local reference before any external effect. The byte cache
    # reuses only stable inode/size/mtime/ctime receipts, never filenames alone.
    if index is not None:
        for relative, expected in objects.items():
            path = objects_root / relative.removeprefix("objects/")
            if path.is_symlink() or not path.resolve().is_relative_to(objects_root.resolve()) or not path.is_file():
                raise ValueError(f"Missing immutable surveillance object: {relative}")
            actual = index.describe(path)
            if any(actual[key] != expected[key] for key in ("sha256", "bytes")):
                raise ValueError(f"Surveillance object checksum mismatch: {relative}")
        index.save()
    active = client or R2S3Client(config)
    try:
        with _publication_lock(directory, config, prefix):
            captured, starting_previous = _starting_baseline(directory, config, prefix)
            if require_starting_baseline and not captured:
                raise ValueError("Managed JSON publication requires a starting baseline captured before generation")
            pointer = f"{prefix}/current.json"
            previous = active.get_object(pointer)
            previous_objects = {}
            if previous is not None:
                _, previous_objects = validate_manifest(previous)
            if previous == body:
                return {"release_id": manifest["release_id"], "uploaded": 0,
                        "unchanged": len(objects), "bytes_uploaded": 0, "pointer_changed": False}
            if captured and previous != starting_previous:
                raise R2S3Error("Public JSON changed since the starting baseline was captured before generation", status=412)
            candidate_sha = hashlib.sha256(body).hexdigest()
            baseline_sha = hashlib.sha256(previous).hexdigest() if previous is not None else None
            receipt_dir = directory / ".publication"
            receipt_dir.mkdir(parents=True, exist_ok=True)
            receipt_path = receipt_dir / f"{candidate_sha}.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if (receipt.get("schema") != 1 or receipt.get("candidate_sha256") != candidate_sha
                        or receipt.get("baseline_sha256") != baseline_sha):
                    raise R2S3Error("A newer JSON publication invalidated this candidate's original baseline", status=412)
            else:
                # Persist the first observed baseline before uploading anything.
                # An old interrupted candidate must never adopt a newer release
                # as its baseline on retry and roll the pointer backwards.
                _atomic_receipt(receipt_path, {"schema": 1, "candidate_sha256": candidate_sha,
                    "baseline_sha256": baseline_sha})
            # The small remote intent retains the same fence if a local cache
            # is lost or a candidate is resumed from another machine.
            intent = json.dumps({"schema": 1, "candidate_sha256": candidate_sha,
                "baseline_sha256": baseline_sha}, sort_keys=True, separators=(",", ":")).encode()
            intent_key = f"{prefix}/intents/{candidate_sha}.json"
            try:
                active.put_object_conditional(intent_key, intent, previous=None, cache_control="no-store")
            except R2S3Error as error:
                if error.status != 412:
                    raise
                if active.get_object(intent_key) != intent:
                    raise R2S3Error("Candidate no longer matches its original remote publication baseline", status=412) from error
            selected = [(relative, entry) for relative, entry in objects.items()
                        if previous_objects.get(relative) != entry]

            def upload(item):
                relative, expected = item
                path = objects_root / relative.removeprefix("objects/")
                payload = path.read_bytes()
                if not trust_frozen_objects and (len(payload) != expected["bytes"] or hashlib.sha256(payload).hexdigest() != expected["sha256"]):
                    raise ValueError(f"Surveillance object changed during upload: {relative}")
                # Content-addressed keys are create-once; a failed prior attempt
                # may already have uploaded one. Never overwrite different bytes.
                key = f"{prefix}/{relative}"
                encoded = gzip.compress(payload, compresslevel=6, mtime=0) if len(payload) > 1024 else payload
                compressed = len(encoded) < len(payload)
                wire = encoded if compressed else payload
                try:
                    active.put_object_conditional(key, wire, previous=None,
                        cache_control="public, max-age=31536000, immutable",
                        content_encoding="gzip" if compressed else None)
                except R2S3Error as error:
                    if error.status != 412 or (not trust_frozen_objects and active.get_object(key) != payload):
                        raise
                    return 0, 0
                return 1, len(wire)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(upload, selected))
            uploaded = sum(count for count, _ in results)
            transferred = sum(size for _, size in results)
            # Retain immutable version manifests for audit/rollback. No garbage
            # collection is coupled to publication, so open pages keep resolving.
            version = f"{prefix}/manifests/{candidate_sha}.json"
            try:
                active.put_object_conditional(version, body, previous=None,
                    cache_control="public, max-age=31536000, immutable")
            except R2S3Error as error:
                if error.status != 412 or active.get_object(version) != body:
                    raise
            # This is the only mutable key. A competing publisher cannot silently
            # overwrite a newer pointer; an ambiguous successful PUT is resumable.
            try:
                active.put_object_conditional(pointer, body, previous=previous, cache_control="no-store")
            except R2S3Error as error:
                if error.status != 412 or active.get_object(pointer) != body:
                    raise
            if trust_frozen_objects:
                logger.info("Research JSON published: release={} uploaded={} reused={} bytes={} seconds={:.2f}",
                            manifest["release_id"], uploaded, len(objects) - uploaded, transferred,
                            time.monotonic() - started)
            return {"release_id": manifest["release_id"], "uploaded": uploaded,
                    "unchanged": len(objects) - uploaded, "bytes_uploaded": transferred,
                    "pointer_changed": True, "manifest_sha256": hashlib.sha256(body).hexdigest()}
    finally:
        if client is None:
            active.close()
