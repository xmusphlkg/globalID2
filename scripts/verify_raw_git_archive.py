#!/usr/bin/env python3
"""Verify or restore a GlobalID ``raw-v1`` content-addressed snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


PROTOCOL = "globalid.raw-git-archive.v1"
POINTER_PROTOCOL = "globalid.raw-git-archive.pointer.v1"
MARKER_FILE = ".globalid-raw-git-archive-v1"
MARKER_CONTENT = "globalid-raw-git-archive-v1\n"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RawArchiveValidationError(RuntimeError):
    """Raised when a snapshot cannot be verified safely."""


@dataclass(frozen=True)
class VerificationResult:
    archive_id: str
    manifest: str
    file_count: int
    source_bytes: int
    object_count: int
    compressed_bytes: int
    restored_to: str | None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RawArchiveValidationError(f"Unsafe relative path: {value!r}")
    return path


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawArchiveValidationError(f"Unable to read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RawArchiveValidationError(f"Expected a JSON object: {path}")
    return payload


def _restore_parent_without_symlinks(
    restore_root: Path,
    relative: PurePosixPath,
) -> Path:
    """Create a restore parent without following directory symlinks."""
    current = restore_root
    for part in relative.parent.parts:
        current = current / part
        if current.is_symlink():
            raise RawArchiveValidationError(f"Refusing to restore through symlink: {current}")
        if current.exists():
            if not current.is_dir():
                raise RawArchiveValidationError(f"Restore parent is not a directory: {current}")
            continue
        current.mkdir()
    return current


def _resolve_manifest(
    repository: Path,
    manifest: Path | None,
) -> tuple[Path, dict[str, object]]:
    if manifest is not None:
        path = manifest if manifest.is_absolute() else repository / manifest
        resolved = path.resolve()
        try:
            resolved.relative_to(repository)
        except ValueError as exc:
            raise RawArchiveValidationError("Manifest must be inside the archive repository") from exc
        return resolved, {}
    pointer_path = repository / "latest.json"
    pointer = _read_json(pointer_path)
    if pointer.get("protocol") != POINTER_PROTOCOL:
        raise RawArchiveValidationError(f"Invalid latest pointer protocol: {pointer_path}")
    relative = _safe_relative(str(pointer.get("manifest") or ""))
    path = repository.joinpath(*relative.parts)
    if path.stat().st_size != int(pointer.get("manifest_size") or -1):
        raise RawArchiveValidationError("Latest manifest size does not match its pointer")
    expected_sha = str(pointer.get("manifest_sha256") or "")
    if not SHA256_PATTERN.fullmatch(expected_sha) or _sha256_file(path) != expected_sha:
        raise RawArchiveValidationError("Latest manifest SHA-256 does not match its pointer")
    return path.resolve(), pointer


def _object_tempfile(
    repository: Path,
    chunks: list[dict[str, object]],
) -> Path:
    with tempfile.NamedTemporaryFile(prefix="globalid-raw-verify-", delete=False) as handle:
        temporary = Path(handle.name)
        process = subprocess.Popen(
            ["zstd", "--quiet", "--decompress", "--stdout"],
            stdin=subprocess.PIPE,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
        try:
            assert process.stdin is not None
            for chunk in chunks:
                relative = _safe_relative(str(chunk.get("path") or ""))
                path = repository.joinpath(*relative.parts)
                expected_size = int(chunk.get("size") or -1)
                expected_sha = str(chunk.get("sha256") or "")
                if not path.is_file() or path.stat().st_size != expected_size:
                    raise RawArchiveValidationError(f"Missing or truncated object chunk: {relative}")
                if not SHA256_PATTERN.fullmatch(expected_sha) or _sha256_file(path) != expected_sha:
                    raise RawArchiveValidationError(f"Object chunk SHA-256 mismatch: {relative}")
                with path.open("rb") as chunk_handle:
                    shutil.copyfileobj(chunk_handle, process.stdin, length=4 * 1024 * 1024)
            process.stdin.close()
            stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            returncode = process.wait()
            if returncode != 0:
                raise RawArchiveValidationError(
                    f"zstd decompression failed with exit code {returncode}: {stderr.strip()}"
                )
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def verify_raw_archive(
    repository_dir: Path,
    *,
    manifest: Path | None = None,
    source_dir: Path | None = None,
    restore_dir: Path | None = None,
) -> VerificationResult:
    repository = repository_dir.resolve()
    marker = repository / MARKER_FILE
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RawArchiveValidationError(f"Missing or invalid archive marker: {marker}")
    if shutil.which("zstd") is None:
        raise RawArchiveValidationError("zstd executable is required")

    manifest_path, pointer = _resolve_manifest(repository, manifest)
    payload = _read_json(manifest_path)
    if payload.get("protocol") != PROTOCOL:
        raise RawArchiveValidationError(f"Invalid manifest protocol: {manifest_path}")
    files = payload.get("files")
    if not isinstance(files, list) or int(payload.get("file_count") or -1) != len(files):
        raise RawArchiveValidationError("Manifest file count mismatch")

    source_root = source_dir.resolve() if source_dir is not None else None
    restore_root = (
        Path(os.path.abspath(os.fspath(restore_dir)))
        if restore_dir is not None
        else None
    )
    if restore_root is not None:
        if restore_root.is_symlink():
            raise RawArchiveValidationError(f"Restore directory must not be a symlink: {restore_root}")
        restore_root.mkdir(parents=True, exist_ok=True)
        if not restore_root.is_dir():
            raise RawArchiveValidationError(f"Restore path is not a directory: {restore_root}")

    seen_paths: set[str] = set()
    verified_objects: dict[str, Path] = {}
    temporary_objects: list[Path] = []
    source_bytes = 0
    compressed_bytes = 0
    try:
        for item in files:
            if not isinstance(item, dict):
                raise RawArchiveValidationError("Manifest contains an invalid file entry")
            relative = _safe_relative(str(item.get("path") or ""))
            if "_auth" in relative.parts or relative.as_posix() in seen_paths:
                raise RawArchiveValidationError(f"Excluded or duplicate source path: {relative}")
            seen_paths.add(relative.as_posix())
            size = int(item.get("size", -1))
            digest = str(item.get("sha256") or "")
            if size < 0 or not SHA256_PATTERN.fullmatch(digest):
                raise RawArchiveValidationError(f"Invalid source identity: {relative}")
            source_bytes += size

            object_payload = item.get("object")
            if not isinstance(object_payload, dict):
                raise RawArchiveValidationError(f"Missing object metadata: {relative}")
            chunks = object_payload.get("chunks")
            if (
                object_payload.get("codec") != "zstd"
                or object_payload.get("source_sha256") != digest
                or int(object_payload.get("source_size", -1)) != size
                or not isinstance(chunks, list)
                or not chunks
                or not all(isinstance(chunk, dict) for chunk in chunks)
            ):
                raise RawArchiveValidationError(f"Invalid object metadata: {relative}")

            if digest not in verified_objects:
                object_compressed_bytes = sum(int(chunk.get("size", -1)) for chunk in chunks)
                if object_compressed_bytes != int(object_payload.get("compressed_size", -1)):
                    raise RawArchiveValidationError(f"Compressed size mismatch: {relative}")
                compressed_bytes += object_compressed_bytes
                temporary = _object_tempfile(repository, chunks)
                temporary_objects.append(temporary)
                if temporary.stat().st_size != size or _sha256_file(temporary) != digest:
                    raise RawArchiveValidationError(f"Restored source hash mismatch: {relative}")
                verified_objects[digest] = temporary

            if source_root is not None:
                source_path = source_root.joinpath(*relative.parts)
                if (
                    not source_path.is_file()
                    or source_path.stat().st_size != size
                    or _sha256_file(source_path) != digest
                ):
                    raise RawArchiveValidationError(f"Live source does not match archive: {relative}")
            if restore_root is not None:
                destination_parent = _restore_parent_without_symlinks(restore_root, relative)
                destination = destination_parent / relative.name
                if destination.is_symlink():
                    raise RawArchiveValidationError(f"Restore destination must not be a symlink: {destination}")
                temporary_destination = destination.with_suffix(destination.suffix + ".tmp")
                if temporary_destination.is_symlink():
                    raise RawArchiveValidationError(
                        f"Restore temporary path must not be a symlink: {temporary_destination}"
                    )
                shutil.copy2(verified_objects[digest], temporary_destination)
                temporary_destination.replace(destination)
    finally:
        for temporary in temporary_objects:
            temporary.unlink(missing_ok=True)

    if source_bytes != int(payload.get("source_bytes", -1)):
        raise RawArchiveValidationError("Manifest source byte count mismatch")
    if pointer:
        if pointer.get("archive_id") != payload.get("archive_id"):
            raise RawArchiveValidationError("Latest pointer archive ID mismatch")
        if int(pointer.get("file_count", -1)) != len(files):
            raise RawArchiveValidationError("Latest pointer file count mismatch")

    return VerificationResult(
        archive_id=str(payload.get("archive_id") or ""),
        manifest=str(manifest_path.relative_to(repository)),
        file_count=len(files),
        source_bytes=source_bytes,
        object_count=len(verified_objects),
        compressed_bytes=compressed_bytes,
        restored_to=str(restore_root) if restore_root is not None else None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--restore-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = verify_raw_archive(
        args.repository_dir,
        manifest=args.manifest,
        source_dir=args.source_dir,
        restore_dir=args.restore_dir,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
