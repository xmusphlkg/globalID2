"""Freeze file identities, copy atomically and split raw bytes without data loss."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import BinaryIO, Iterator

from .contracts import LargeFileRecord, PartRecord, RawArchiveError


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity(path: Path, *, sha256_file=_sha256_file) -> tuple[int, str]:
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RawArchiveError(f"Source file changed while it was being read: {path}")
    return after.st_size, digest


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RawArchiveError(f"Unsafe archive path: {value!r}")
    return path


def iter_source_files(source_dir: Path) -> Iterator[tuple[Path, Path]]:
    """Yield source bytes, excluding authentication and private model requests."""

    source = source_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Raw source directory does not exist: {source}")
    files: list[tuple[Path, Path]] = []
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(source)
        if "_auth" in relative.parts or relative.parts[0] == "ai":
            continue
        if candidate.is_symlink():
            raise RawArchiveError(f"Symlinks are forbidden in raw archives: {candidate}")
        if candidate.is_file():
            files.append((relative, candidate))
    yield from sorted(files, key=lambda item: item[0].as_posix())


def _atomic_copy(source: Path, destination: Path, *, size: int, sha256: str, sha256_file=_sha256_file) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.sync-", dir=destination.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle, length=4 * 1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.stat().st_size != size or sha256_file(temporary) != sha256:
            raise RawArchiveError(f"Source changed while it was copied: {source}")
        shutil.copystat(source, temporary)
        temporary.replace(destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _bounded_copies(workers: int, *, copy_file=_atomic_copy):
    """Keep at most two copy jobs per worker; join before metadata or Git writes."""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raw-copy") as pool:
        pending = set()

        def drain():
            nonlocal pending
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()

        def copy(source, destination, *, size, sha256):
            if len(pending) >= 2 * workers:
                drain()
            future = pool.submit(copy_file, source, destination, size=size, sha256=sha256)
            pending.add(future)
            return future

        try:
            yield copy
            while pending:
                drain()
        finally:
            # Running atomic copies finish while the repository lock is held.
            # A failed copy prevents every metadata write, commit and push.
            for future in pending:
                future.cancel()


class _PartWriter:
    def __init__(self, directory: Path, relative_prefix: PurePosixPath, *, part_bytes: int = 48 * 1024 * 1024):
        self.part_bytes = part_bytes
        self.directory = directory
        self.relative_prefix = relative_prefix
        self.handle: BinaryIO | None = None
        self.digest: hashlib._Hash | None = None
        self.size = 0
        self.index = 0
        self.parts: list[PartRecord] = []

    def _open(self) -> None:
        self.index += 1
        self.handle = (self.directory / f"part-{self.index:04d}").open("wb")
        self.digest = hashlib.sha256()
        self.size = 0

    def _close(self) -> None:
        if self.handle is None or self.digest is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        name = f"part-{self.index:04d}"
        self.parts.append(
            PartRecord(
                path=(self.relative_prefix / name).as_posix(),
                size=self.size,
                sha256=self.digest.hexdigest(),
            )
        )
        self.handle = None
        self.digest = None
        self.size = 0

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self.handle is None:
                self._open()
            available = self.part_bytes - self.size
            piece = view[:available]
            written = self.handle.write(piece)
            if written is None:
                written = len(piece)
            assert self.digest is not None
            self.digest.update(piece[:written])
            self.size += written
            view = view[written:]
            if self.size == self.part_bytes:
                self._close()

    def close(self) -> tuple[PartRecord, ...]:
        self._close()
        return tuple(self.parts)


def _parts_are_current(repository_dir: Path, record: LargeFileRecord, *, sha256_file=_sha256_file, safe_relative_path=_safe_relative_path) -> bool:
    if not record.parts:
        return False
    for part in record.parts:
        relative = safe_relative_path(part.path)
        path = repository_dir.joinpath(*relative.parts)
        if (
            not path.is_file()
            or path.stat().st_size != part.size
            or sha256_file(path) != part.sha256
        ):
            return False
    return sum(part.size for part in record.parts) == record.size


def _split_file(
    source: Path,
    repository_dir: Path,
    relative: PurePosixPath,
    *,
    size: int,
    sha256: str,
    data_root: PurePosixPath = PurePosixPath("data/raw"),
    writer_factory=_PartWriter,
) -> LargeFileRecord:
    split_relative = data_root / PurePosixPath(f"{relative.as_posix()}.parts")
    destination = repository_dir.joinpath(*split_relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.sync-", dir=destination.parent))
    writer = writer_factory(temporary, split_relative)
    combined_digest = hashlib.sha256()
    combined_size = 0
    try:
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                combined_digest.update(block)
                combined_size += len(block)
                writer.write(block)
        parts = writer.close()
        if combined_size != size or combined_digest.hexdigest() != sha256:
            raise RawArchiveError(f"Source changed while it was split: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        temporary.rename(destination)
        return LargeFileRecord(path=relative.as_posix(), size=size, sha256=sha256, parts=parts)
    except Exception:
        writer.close()
        shutil.rmtree(temporary, ignore_errors=True)
        raise
