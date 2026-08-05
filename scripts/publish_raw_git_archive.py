#!/usr/bin/env python3
"""Incrementally archive ``data/raw`` to a dedicated Git branch.

The archive is content-addressed: unchanged source bytes reuse existing
compressed objects, while every snapshot manifest remains available for
point-in-time restores.  Compressed streams are split below GitHub's per-file
limit.  The command updates a persistent local clone and only contacts the
remote when ``--push`` is supplied.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import BinaryIO, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "data" / "raw"
DEFAULT_REPOSITORY_DIR = ROOT / "exports" / "raw-git-archive"
DEFAULT_REPO_URL = os.getenv("RAW_ARCHIVE__REPO_URL", "").strip()
TARGET_BRANCH = "raw-v1"
PROTOCOL = "globalid.raw-git-archive.v1"
POINTER_PROTOCOL = "globalid.raw-git-archive.pointer.v1"
MARKER_FILE = ".globalid-raw-git-archive-v1"
MARKER_CONTENT = "globalid-raw-git-archive-v1\n"
DEFAULT_CHUNK_BYTES = 48 * 1024 * 1024
DEFAULT_COMMIT_BATCH_BYTES = 96 * 1024 * 1024
DEFAULT_GIT_TIMEOUT_SECONDS = 30 * 60
DEFAULT_ZSTD_LEVEL = 6
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RawArchiveError(RuntimeError):
    """Raised when a raw archive cannot be safely built or published."""


@dataclass(frozen=True)
class ChunkRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ObjectRecord:
    source_sha256: str
    source_size: int
    codec: str
    compressed_size: int
    chunks: tuple[ChunkRecord, ...]


@dataclass(frozen=True)
class SourceRecord:
    path: str
    size: int
    mtime_ns: int
    sha256: str
    object: ObjectRecord


@dataclass(frozen=True)
class ArchiveResult:
    mode: str
    branch: str
    archive_id: str | None
    source_file_count: int
    source_bytes: int
    new_object_count: int
    reused_object_count: int
    compressed_bytes_added: int
    manifest_path: str | None
    commit_count: int
    changed: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RawArchiveError(f"Unsafe archive path: {value!r}")
    return path


def iter_source_files(source_dir: Path) -> Iterator[tuple[Path, Path]]:
    """Yield safe source files in stable path order, excluding auth captures."""

    source = source_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Raw source directory does not exist: {source}")
    files: list[tuple[Path, Path]] = []
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(source)
        if "_auth" in relative.parts:
            continue
        if candidate.is_symlink():
            raise RawArchiveError(f"Symlinks are forbidden in raw archives: {candidate}")
        if candidate.is_file():
            files.append((relative, candidate))
    yield from sorted(files, key=lambda item: item[0].as_posix())


def _object_dir(repository_dir: Path, source_sha256: str) -> Path:
    if not SHA256_PATTERN.fullmatch(source_sha256):
        raise RawArchiveError(f"Invalid source SHA-256: {source_sha256!r}")
    return repository_dir / "objects" / "sha256" / source_sha256[:2] / source_sha256


def _object_metadata_path(repository_dir: Path, source_sha256: str) -> Path:
    return _object_dir(repository_dir, source_sha256) / "object.json"


def _load_object(
    repository_dir: Path,
    source_sha256: str,
    *,
    verify_hashes: bool = False,
) -> ObjectRecord | None:
    metadata_path = _object_metadata_path(repository_dir, source_sha256)
    if not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        chunks = tuple(
            ChunkRecord(
                path=str(item["path"]),
                size=int(item["size"]),
                sha256=str(item["sha256"]),
            )
            for item in payload["chunks"]
        )
        record = ObjectRecord(
            source_sha256=str(payload["source_sha256"]),
            source_size=int(payload["source_size"]),
            codec=str(payload["codec"]),
            compressed_size=int(payload["compressed_size"]),
            chunks=chunks,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RawArchiveError(f"Invalid object metadata: {metadata_path}") from exc
    if record.source_sha256 != source_sha256 or record.codec != "zstd":
        raise RawArchiveError(f"Object identity mismatch: {metadata_path}")
    if not record.chunks:
        raise RawArchiveError(f"Object has no chunks: {metadata_path}")
    compressed_size = 0
    for chunk in record.chunks:
        relative = _safe_relative_path(chunk.path)
        path = repository_dir.joinpath(*relative.parts)
        if (
            not path.is_file()
            or path.stat().st_size != chunk.size
            or not SHA256_PATTERN.fullmatch(chunk.sha256)
            or (verify_hashes and _sha256_file(path) != chunk.sha256)
        ):
            raise RawArchiveError(f"Stored object chunk failed validation: {path}")
        compressed_size += chunk.size
    if compressed_size != record.compressed_size:
        raise RawArchiveError(f"Compressed byte count mismatch: {metadata_path}")
    return record


class _ChunkWriter:
    def __init__(self, directory: Path, relative_prefix: PurePosixPath, limit: int):
        if limit < 1:
            raise ValueError("chunk limit must be positive")
        self.directory = directory
        self.relative_prefix = relative_prefix
        self.limit = limit
        self._handle: BinaryIO | None = None
        self._digest: hashlib._Hash | None = None
        self._size = 0
        self._index = 0
        self.records: list[ChunkRecord] = []

    def _open_next(self) -> None:
        name = f"payload.zst.part{self._index:04d}"
        self._index += 1
        self._handle = (self.directory / name).open("wb")
        self._digest = hashlib.sha256()
        self._size = 0

    def _finish_current(self) -> None:
        if self._handle is None or self._digest is None:
            return
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        name = f"payload.zst.part{self._index - 1:04d}"
        self.records.append(
            ChunkRecord(
                path=(self.relative_prefix / name).as_posix(),
                size=self._size,
                sha256=self._digest.hexdigest(),
            )
        )
        self._handle = None
        self._digest = None
        self._size = 0

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self._handle is None:
                self._open_next()
            available = self.limit - self._size
            piece = view[:available]
            written = self._handle.write(piece)
            if written is None:
                written = len(piece)
            raw_piece = piece[:written]
            assert self._digest is not None
            self._digest.update(raw_piece)
            self._size += written
            view = view[written:]
            if self._size == self.limit:
                self._finish_current()

    def close(self) -> tuple[ChunkRecord, ...]:
        self._finish_current()
        return tuple(self.records)


def _compress_object(
    source_path: Path,
    repository_dir: Path,
    source_sha256: str,
    *,
    chunk_bytes: int,
    zstd_level: int,
) -> ObjectRecord:
    destination = _object_dir(repository_dir, source_sha256)
    relative_prefix = PurePosixPath(destination.relative_to(repository_dir).as_posix())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{source_sha256}.incomplete-", dir=destination.parent)
    )
    writer = _ChunkWriter(temporary, relative_prefix, chunk_bytes)
    command = [
        "zstd",
        "--quiet",
        "--threads=0",
        f"-{zstd_level}",
        "--stdout",
        "--",
        str(source_path),
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        for block in iter(lambda: process.stdout.read(4 * 1024 * 1024), b""):
            writer.write(block)
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        returncode = process.wait()
        chunks = writer.close()
        if returncode != 0:
            raise RawArchiveError(
                f"zstd failed for {source_path} with exit code {returncode}: {stderr.strip()}"
            )
        if not chunks:
            raise RawArchiveError(f"zstd produced no output for {source_path}")
        record = ObjectRecord(
            source_sha256=source_sha256,
            source_size=source_path.stat().st_size,
            codec="zstd",
            compressed_size=sum(item.size for item in chunks),
            chunks=chunks,
        )
        (temporary / "object.json").write_text(
            json.dumps(
                {
                    **asdict(record),
                    "restore": "cat payload.zst.part* | zstd --decompress --stdout",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            shutil.rmtree(destination)
        temporary.rename(destination)
        return record
    except Exception:
        writer.close()
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_environment(),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RawArchiveError(
            f"git {' '.join(args)} timed out after {timeout_seconds:g} seconds"
        ) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RawArchiveError(
            f"git {' '.join(args)} failed with exit code {result.returncode}: {detail}"
        )
    return result


def _configure_archive_git(repository_dir: Path, timeout_seconds: float) -> None:
    run_git(["config", "user.name", "GlobalID Raw Archive"], repository_dir, timeout_seconds=timeout_seconds)
    run_git(["config", "user.email", "raw-archive@globalid.invalid"], repository_dir, timeout_seconds=timeout_seconds)
    # zstd payload chunks are already compressed and do not benefit from Git's
    # expensive delta search or high deflate levels. These settings make large
    # incremental pushes substantially faster without changing archive bytes.
    run_git(["config", "core.compression", "1"], repository_dir, timeout_seconds=timeout_seconds)
    run_git(["config", "pack.compression", "1"], repository_dir, timeout_seconds=timeout_seconds)
    run_git(["config", "pack.window", "0"], repository_dir, timeout_seconds=timeout_seconds)
    run_git(["config", "pack.depth", "0"], repository_dir, timeout_seconds=timeout_seconds)


def _ensure_repository(
    repository_dir: Path,
    *,
    repo_url: str,
    push: bool,
    timeout_seconds: float,
) -> None:
    repository_dir = repository_dir.resolve()
    if (repository_dir / ".git").is_dir():
        branch = run_git(
            ["symbolic-ref", "--short", "HEAD"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        ).stdout.strip()
        if branch != TARGET_BRANCH:
            raise RawArchiveError(
                f"Archive clone is on {branch!r}, expected {TARGET_BRANCH!r}: {repository_dir}"
            )
        configured = run_git(
            ["remote", "get-url", "origin"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if push and configured.returncode != 0:
            run_git(
                ["remote", "add", "origin", repo_url],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
        elif push and configured.stdout.strip() != repo_url:
            raise RawArchiveError(
                f"Archive origin mismatch: {configured.stdout.strip()!r} != {repo_url!r}"
            )
        tracked_objects = run_git(
            ["status", "--porcelain=v1", "--untracked-files=no", "--", "objects"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        ).stdout.strip()
        if tracked_objects:
            raise RawArchiveError(
                "Tracked content-addressed objects were modified locally; "
                "refusing to archive over possible corruption"
            )
        if push:
            remote = run_git(
                ["ls-remote", "--heads", "--", repo_url, f"refs/heads/{TARGET_BRANCH}"],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
            if remote.stdout.strip():
                run_git(
                    ["fetch", "origin", TARGET_BRANCH],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                )
                remote_is_ancestor = run_git(
                    ["merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                    check=False,
                )
                if remote_is_ancestor.returncode != 0:
                    local_is_ancestor = run_git(
                        ["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"],
                        repository_dir,
                        timeout_seconds=timeout_seconds,
                        check=False,
                    )
                    if local_is_ancestor.returncode == 0:
                        run_git(
                            ["merge", "--ff-only", "FETCH_HEAD"],
                            repository_dir,
                            timeout_seconds=timeout_seconds,
                        )
                    else:
                        raise RawArchiveError(
                            "Local and remote raw-v1 histories diverged; refusing to overwrite either side"
                        )
        _configure_archive_git(repository_dir, timeout_seconds)
        return

    if repository_dir.exists() and any(repository_dir.iterdir()):
        raise RawArchiveError(f"Archive repository directory is not empty: {repository_dir}")
    repository_dir.mkdir(parents=True, exist_ok=True)

    remote_branch_exists = False
    if push:
        if not repo_url:
            raise RawArchiveError("--push requires --repo-url or RAW_ARCHIVE__REPO_URL")
        probe = run_git(
            ["ls-remote", "--heads", "--", repo_url, f"refs/heads/{TARGET_BRANCH}"],
            ROOT,
            timeout_seconds=timeout_seconds,
        )
        remote_branch_exists = bool(probe.stdout.strip())

    if remote_branch_exists:
        repository_dir.rmdir()
        run_git(
            ["clone", "--branch", TARGET_BRANCH, "--single-branch", "--", repo_url, str(repository_dir)],
            ROOT,
            timeout_seconds=timeout_seconds,
        )
    else:
        run_git(["init", "--quiet"], repository_dir, timeout_seconds=timeout_seconds)
        run_git(
            ["checkout", "--orphan", TARGET_BRANCH],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
        if push:
            run_git(
                ["remote", "add", "origin", repo_url],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )

    _configure_archive_git(repository_dir, timeout_seconds)


def _commit_paths(
    repository_dir: Path,
    paths: list[str],
    message: str,
    *,
    push: bool,
    timeout_seconds: float,
) -> bool:
    if not paths:
        return False
    run_git(["add", "--", *paths], repository_dir, timeout_seconds=timeout_seconds)
    staged = run_git(
        ["diff", "--cached", "--quiet"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        raise RawArchiveError("Unable to inspect staged archive changes")
    run_git(
        ["commit", "--no-gpg-sign", "-m", message],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    if push:
        run_git(
            ["push", "origin", f"HEAD:refs/heads/{TARGET_BRANCH}"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
    return True


def _load_latest_manifest(repository_dir: Path) -> dict[str, object] | None:
    pointer_path = repository_dir / "latest.json"
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        manifest_relative = _safe_relative_path(str(pointer["manifest"]))
        manifest_path = repository_dir.joinpath(*manifest_relative.parts)
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RawArchiveError(f"Invalid latest archive pointer: {pointer_path}") from exc


def _same_snapshot(previous: dict[str, object] | None, files: list[SourceRecord]) -> bool:
    if previous is None:
        return False
    previous_files = previous.get("files")
    if not isinstance(previous_files, list) or len(previous_files) != len(files):
        return False
    previous_identity = [
        (str(item.get("path")), int(item.get("size", -1)), str(item.get("sha256")))
        for item in previous_files
        if isinstance(item, dict)
    ]
    current_identity = [(item.path, item.size, item.sha256) for item in files]
    return previous_identity == current_identity


def _upgrade_latest_pointer(repository_dir: Path) -> tuple[bool, str | None, str | None]:
    """Backfill manifest integrity fields written by early raw-v1 publishers."""

    pointer_path = repository_dir / "latest.json"
    if not pointer_path.is_file():
        return False, None, None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        manifest_relative = _safe_relative_path(str(pointer["manifest"]))
        manifest_path = repository_dir.joinpath(*manifest_relative.parts)
        manifest_size = manifest_path.stat().st_size
        manifest_sha256 = _sha256_file(manifest_path)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RawArchiveError(f"Invalid latest archive pointer: {pointer_path}") from exc
    if (
        pointer.get("manifest_size") == manifest_size
        and pointer.get("manifest_sha256") == manifest_sha256
    ):
        return False, str(pointer.get("archive_id") or ""), manifest_relative.as_posix()
    pointer["manifest_size"] = manifest_size
    pointer["manifest_sha256"] = manifest_sha256
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True, str(pointer.get("archive_id") or ""), manifest_relative.as_posix()


def _write_archive_metadata(repository_dir: Path) -> None:
    marker = repository_dir / MARKER_FILE
    if marker.exists() and marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RawArchiveError(f"Archive marker has unexpected content: {marker}")
    marker.write_text(MARKER_CONTENT, encoding="utf-8")
    readme = repository_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# GlobalID raw data archive\n\n"
            "This branch is maintained automatically by the GlobalID platform. "
            "It uses content-addressed zstd objects split into GitHub-safe chunks; "
            "do not edit it manually.\n\n"
            "To restore one file, concatenate the object's chunks in manifest order "
            "and pipe them to `zstd --decompress --stdout`. Paths containing `_auth` "
            "are intentionally excluded.\n",
            encoding="utf-8",
        )


def publish_raw_archive(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    repository_dir: Path = DEFAULT_REPOSITORY_DIR,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    push: bool = False,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    commit_batch_bytes: int = DEFAULT_COMMIT_BATCH_BYTES,
    zstd_level: int = DEFAULT_ZSTD_LEVEL,
    git_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> ArchiveResult:
    if chunk_bytes < 1 or chunk_bytes >= 100 * 1024 * 1024:
        raise ValueError("chunk_bytes must be between 1 byte and GitHub's 100 MiB limit")
    if commit_batch_bytes < chunk_bytes:
        raise ValueError("commit_batch_bytes must be at least chunk_bytes")
    if not 1 <= zstd_level <= 19:
        raise ValueError("zstd_level must be between 1 and 19")
    if shutil.which("zstd") is None:
        raise RawArchiveError("zstd executable is required")

    source_root = Path(source_dir).resolve()
    repository_root = Path(repository_dir).resolve()
    lock_path = repository_root.parent / f".{repository_root.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RawArchiveError(f"Another raw archive publisher holds {lock_path}") from exc

        _ensure_repository(
            repository_root,
            repo_url=repo_url.strip(),
            push=push,
            timeout_seconds=git_timeout_seconds,
        )
        _write_archive_metadata(repository_root)
        previous = _load_latest_manifest(repository_root)
        tracked_object_metadata = {
            line.strip()
            for line in run_git(
                ["ls-files", "--", "objects"],
                repository_root,
                timeout_seconds=git_timeout_seconds,
            ).stdout.splitlines()
            if line.strip().endswith("/object.json")
        }

        files: list[SourceRecord] = []
        new_objects: list[tuple[str, int]] = []
        queued_objects: set[str] = set()
        reused = 0
        source_bytes = 0
        for index, (relative, source_path) in enumerate(iter_source_files(source_root), start=1):
            stat = source_path.stat()
            size = stat.st_size
            # Always hash source bytes. Size/mtime caches can miss a replacement
            # made with preserved metadata, which is unacceptable for backup.
            digest = _sha256_file(source_path)
            object_record = None
            source_bytes += size
            if object_record is None:
                object_record = _load_object(repository_root, digest)
            if object_record is None:
                print(f"compress {index}: {relative.as_posix()} ({size} bytes)", flush=True)
                object_record = _compress_object(
                    source_path,
                    repository_root,
                    digest,
                    chunk_bytes=chunk_bytes,
                    zstd_level=zstd_level,
                )
                object_relative = _object_dir(repository_root, digest).relative_to(repository_root).as_posix()
                if object_relative not in queued_objects:
                    new_objects.append((object_relative, object_record.compressed_size))
                    queued_objects.add(object_relative)
            else:
                if object_record.source_size != size:
                    raise RawArchiveError(f"Source size conflicts with stored object: {relative}")
                object_relative = _object_dir(repository_root, digest).relative_to(repository_root).as_posix()
                metadata_relative = f"{object_relative}/object.json"
                if (
                    metadata_relative not in tracked_object_metadata
                    and object_relative not in queued_objects
                ):
                    new_objects.append((object_relative, object_record.compressed_size))
                    queued_objects.add(object_relative)
                else:
                    reused += 1
            files.append(
                SourceRecord(
                    path=relative.as_posix(),
                    size=size,
                    mtime_ns=stat.st_mtime_ns,
                    sha256=digest,
                    object=object_record,
                )
            )

        if not new_objects and _same_snapshot(previous, files):
            pointer_updated, current_archive_id, current_manifest_path = _upgrade_latest_pointer(
                repository_root
            )
            commit_count = 0
            if pointer_updated and _commit_paths(
                repository_root,
                ["latest.json"],
                "archive(metadata): add manifest integrity pointer",
                push=push,
                timeout_seconds=git_timeout_seconds,
            ):
                commit_count = 1
            return ArchiveResult(
                mode="pushed" if pointer_updated and push else (
                    "local" if pointer_updated else "unchanged"
                ),
                branch=TARGET_BRANCH,
                archive_id=current_archive_id,
                source_file_count=len(files),
                source_bytes=source_bytes,
                new_object_count=0,
                reused_object_count=reused,
                compressed_bytes_added=0,
                manifest_path=current_manifest_path,
                commit_count=commit_count,
                changed=pointer_updated,
            )

        now = datetime.now(timezone.utc)
        archive_id = f"raw-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        manifest_relative = PurePosixPath("manifests") / now.strftime("%Y") / now.strftime("%m") / f"{archive_id}.json"
        manifest_path = repository_root.joinpath(*manifest_relative.parts)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "protocol": PROTOCOL,
            "archive_id": archive_id,
            "created_at": now.isoformat(),
            "source_layout": "data/raw",
            "excluded_path_components": ["_auth"],
            "branch": TARGET_BRANCH,
            "file_count": len(files),
            "source_bytes": source_bytes,
            "compressed_bytes": sum(item.object.compressed_size for item in files),
            "files": [asdict(item) for item in files],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_size = manifest_path.stat().st_size
        manifest_sha256 = _sha256_file(manifest_path)
        (repository_root / "latest.json").write_text(
            json.dumps(
                {
                    "protocol": POINTER_PROTOCOL,
                    "archive_id": archive_id,
                    "created_at": now.isoformat(),
                    "manifest": manifest_relative.as_posix(),
                    "manifest_size": manifest_size,
                    "manifest_sha256": manifest_sha256,
                    "file_count": len(files),
                    "source_bytes": source_bytes,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        commit_count = 0
        batch: list[str] = []
        batch_bytes = 0
        for object_path, object_bytes in new_objects:
            if batch and batch_bytes + object_bytes > commit_batch_bytes:
                if _commit_paths(
                    repository_root,
                    batch,
                    f"archive(objects): add {len(batch)} raw object(s)",
                    push=push,
                    timeout_seconds=git_timeout_seconds,
                ):
                    commit_count += 1
                batch = []
                batch_bytes = 0
            batch.append(object_path)
            batch_bytes += object_bytes
        if batch and _commit_paths(
            repository_root,
            batch,
            f"archive(objects): add {len(batch)} raw object(s)",
            push=push,
            timeout_seconds=git_timeout_seconds,
        ):
            commit_count += 1

        metadata_paths = [MARKER_FILE, "README.md", "latest.json", manifest_relative.as_posix()]
        if _commit_paths(
            repository_root,
            metadata_paths,
            f"archive(snapshot): publish {archive_id}",
            push=push,
            timeout_seconds=git_timeout_seconds,
        ):
            commit_count += 1

        return ArchiveResult(
            mode="pushed" if push else "local",
            branch=TARGET_BRANCH,
            archive_id=archive_id,
            source_file_count=len(files),
            source_bytes=source_bytes,
            new_object_count=len(new_objects),
            reused_object_count=reused,
            compressed_bytes_added=sum(item[1] for item in new_objects),
            manifest_path=manifest_relative.as_posix(),
            commit_count=commit_count,
            changed=True,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--repository-dir", type=Path, default=DEFAULT_REPOSITORY_DIR)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--chunk-mib", type=int, default=DEFAULT_CHUNK_BYTES // (1024 * 1024))
    parser.add_argument(
        "--commit-batch-mib",
        type=int,
        default=DEFAULT_COMMIT_BATCH_BYTES // (1024 * 1024),
    )
    parser.add_argument("--zstd-level", type=int, default=DEFAULT_ZSTD_LEVEL)
    parser.add_argument(
        "--git-timeout-seconds",
        type=float,
        default=DEFAULT_GIT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push incremental commits to the dedicated remote branch.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = publish_raw_archive(
        args.source_dir,
        args.repository_dir,
        repo_url=args.repo_url,
        push=args.push,
        chunk_bytes=args.chunk_mib * 1024 * 1024,
        commit_batch_bytes=args.commit_batch_mib * 1024 * 1024,
        zstd_level=args.zstd_level,
        git_timeout_seconds=args.git_timeout_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
