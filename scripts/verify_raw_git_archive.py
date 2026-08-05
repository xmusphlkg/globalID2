#!/usr/bin/env python3
"""Verify or restore a GlobalID readable raw-data mirror."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


DATA_ROOT = PurePosixPath("data/raw")
MARKER_FILE = ".globalid-raw-mirror"
MARKER_CONTENT = "globalid-raw-mirror-v2\n"
LARGE_FILES_INDEX = ".globalid-large-files.json"
PROTOCOL = "globalid.raw-mirror.large-files.v2"


class RawArchiveValidationError(RuntimeError):
    """Raised when a raw-data mirror fails validation."""


@dataclass(frozen=True)
class VerificationResult:
    file_count: int
    source_bytes: int
    direct_file_count: int
    split_file_count: int
    stored_bytes: int
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


def _load_large_files(repository: Path) -> list[dict[str, object]]:
    index_path = repository / LARGE_FILES_INDEX
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawArchiveValidationError(f"Unable to read {index_path}") from exc
    if payload.get("format") != PROTOCOL or not isinstance(payload.get("files"), list):
        raise RawArchiveValidationError(f"Invalid large-file index: {index_path}")
    return payload["files"]


def _iter_source_files(source: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(source)
        if "_auth" in relative.parts:
            continue
        if candidate.is_symlink():
            raise RawArchiveValidationError(f"Symlink is not allowed: {candidate}")
        if candidate.is_file():
            files[relative.as_posix()] = candidate
    return files


def _restore_split_file(
    repository: Path,
    destination: Path | None,
    record: dict[str, object],
) -> tuple[int, str, int]:
    try:
        relative = _safe_relative(str(record["path"]))
        expected_size = int(record["size"])
        expected_sha256 = str(record["sha256"])
        parts = record["parts"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RawArchiveValidationError("Invalid large-file record") from exc
    if not isinstance(parts, list) or not parts:
        raise RawArchiveValidationError(f"Large file has no parts: {relative}")

    digest = hashlib.sha256()
    restored_size = 0
    stored_bytes = 0
    output = None
    temporary = None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.restore-tmp")
            output = temporary.open("wb")
        for part in parts:
            if not isinstance(part, dict):
                raise RawArchiveValidationError(f"Invalid part entry: {relative}")
            part_relative = _safe_relative(str(part.get("path") or ""))
            part_path = repository.joinpath(*part_relative.parts)
            part_size = int(part.get("size", -1))
            part_sha256 = str(part.get("sha256") or "")
            if (
                not part_path.is_file()
                or part_path.stat().st_size != part_size
                or _sha256_file(part_path) != part_sha256
            ):
                raise RawArchiveValidationError(f"Missing or corrupt part: {part_relative}")
            stored_bytes += part_size
            with part_path.open("rb") as handle:
                for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                    digest.update(block)
                    restored_size += len(block)
                    if output is not None:
                        output.write(block)
        if restored_size != expected_size or digest.hexdigest() != expected_sha256:
            raise RawArchiveValidationError(f"Restored hash mismatch: {relative}")
        if output is not None and temporary is not None and destination is not None:
            output.close()
            output = None
            temporary.replace(destination)
        return expected_size, relative.as_posix(), stored_bytes
    except Exception:
        if output is not None:
            output.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def verify_raw_archive(
    repository_dir: Path,
    *,
    source_dir: Path | None = None,
    restore_dir: Path | None = None,
) -> VerificationResult:
    repository = repository_dir.resolve()
    marker = repository / MARKER_FILE
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RawArchiveValidationError(f"Missing or invalid archive marker: {marker}")
    data_root = repository.joinpath(*DATA_ROOT.parts)
    large_files = _load_large_files(repository)
    source_files = _iter_source_files(source_dir.resolve()) if source_dir is not None else None
    restore_root = restore_dir.resolve() if restore_dir is not None else None
    if restore_root is not None:
        restore_root.mkdir(parents=True, exist_ok=True)

    split_paths: set[str] = set()
    split_part_paths: set[str] = set()
    source_bytes = 0
    stored_bytes = 0
    for record in large_files:
        if not isinstance(record, dict):
            raise RawArchiveValidationError("Invalid large-file index entry")
        relative_value = str(record.get("path") or "")
        if relative_value in split_paths:
            raise RawArchiveValidationError(f"Duplicate large-file path: {relative_value}")
        split_paths.add(relative_value)
        parts = record.get("parts")
        if isinstance(parts, list):
            split_part_paths.update(str(part.get("path") or "") for part in parts if isinstance(part, dict))
        destination = (
            restore_root.joinpath(*_safe_relative(relative_value).parts)
            if restore_root is not None
            else None
        )
        size, relative, part_bytes = _restore_split_file(repository, destination, record)
        source_bytes += size
        stored_bytes += part_bytes
        if source_files is not None:
            source_path = source_files.get(relative)
            if (
                source_path is None
                or source_path.stat().st_size != size
                or _sha256_file(source_path) != str(record.get("sha256") or "")
            ):
                raise RawArchiveValidationError(f"Live source does not match archive: {relative}")

    direct_files: dict[str, Path] = {}
    if data_root.exists():
        for candidate in data_root.rglob("*"):
            if not candidate.is_file():
                continue
            repository_relative = candidate.relative_to(repository).as_posix()
            if repository_relative in split_part_paths:
                continue
            relative = candidate.relative_to(data_root).as_posix()
            if relative.endswith(".parts") or ".parts/" in relative:
                raise RawArchiveValidationError(f"Unindexed split part: {repository_relative}")
            direct_files[relative] = candidate

    for relative, archive_path in direct_files.items():
        size = archive_path.stat().st_size
        source_bytes += size
        stored_bytes += size
        if source_files is not None:
            source_path = source_files.get(relative)
            if (
                source_path is None
                or source_path.stat().st_size != size
                or _sha256_file(source_path) != _sha256_file(archive_path)
            ):
                raise RawArchiveValidationError(f"Live source does not match archive: {relative}")
        if restore_root is not None:
            destination = restore_root.joinpath(*_safe_relative(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive_path, destination)

    archived_paths = set(direct_files) | split_paths
    if source_files is not None and archived_paths != set(source_files):
        missing = sorted(set(source_files) - archived_paths)
        extra = sorted(archived_paths - set(source_files))
        raise RawArchiveValidationError(
            f"Archive/source path mismatch; missing={missing[:5]}, extra={extra[:5]}"
        )

    return VerificationResult(
        file_count=len(archived_paths),
        source_bytes=source_bytes,
        direct_file_count=len(direct_files),
        split_file_count=len(split_paths),
        stored_bytes=stored_bytes,
        restored_to=str(restore_root) if restore_root is not None else None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--restore-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = verify_raw_archive(
        args.repository_dir,
        source_dir=args.source_dir,
        restore_dir=args.restore_dir,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
