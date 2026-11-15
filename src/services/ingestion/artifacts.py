"""Durable, immutable source bytes recorded before parsing."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from src.core.database import get_db
from src.domain.collection import SourceArtifact

# capture_sync blocks a default-executor thread until the coroutine finishes.
# The file write and reread must use another pool. On a 4-core runner the
# default pool has 8 threads; eight waiting captures fill it and the writes
# they are waiting for never start.
_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=min(32, (os.cpu_count() or 1) + 4),
    thread_name_prefix="artifact-io",
)


async def run_artifact_io(func, /, *args):
    return await asyncio.get_running_loop().run_in_executor(_IO_EXECUTOR, func, *args)


class ArtifactWriter:
    """Serialize writes on one event loop; blocking callers must use capture_sync.

    A write returns only after the complete file and its hash are registered.
    Cancellation waits for this operation to finish before propagating, so it
    cannot leave a background file write racing with failure cleanup.
    """

    def __init__(self, task_uuid: str, attempt: int, scope: str):
        # Never allow task input to choose an arbitrary filesystem path.
        UUID(task_uuid)
        if not scope or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in scope
        ):
            raise ValueError("Invalid artifact scope")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Artifact attempt must be a positive integer")
        self.task_uuid = task_uuid
        self.attempt = attempt
        self.directory = Path("data/raw") / scope / task_uuid / str(attempt)
        self.artifacts = []
        self._lock = asyncio.Lock()
        self._loop = None
        self._owner_lock = Lock()

    async def write(self, payload: bytes, *, source_url: str, kind: str, metadata=None):
        loop = asyncio.get_running_loop()
        with self._owner_lock:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError(
                    "ArtifactWriter must be used on its owning event loop"
                )
            self._loop = loop
        # Snapshot mutable bytes-like input before handing it to a worker.
        payload = bytes(payload)
        async with self._lock:
            operation = asyncio.create_task(
                self._write(
                    payload, source_url=source_url, kind=kind, metadata=metadata
                )
            )
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # to_thread cannot stop an in-progress filesystem write. Complete
                # file/DB registration (or cleanup) even after repeated cancels.
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        continue
                operation.result()  # Surface persistence failures during cancellation.
                raise

    async def _write(self, payload, *, source_url, kind, metadata):
        digest = hashlib.sha256(payload).hexdigest()
        extension = {
            "csv_page": "csv",
            "archive_json": "json",
            "nhss_xlsx": "xlsx",
            "html_report": "html",
            "workbook_xlsx": "xlsx",
            "workbook_xls": "xls",
            "rki_zip": "zip",
            "cda_xlsx": "xlsx",
            "cda_pdf": "pdf",
            "historical_csv": "csv",
            "history_csv": "csv",
        }.get(kind, "bin")
        # UUIDs also avoid collisions between writer instances for the same run.
        path = self.directory / f"{uuid4().hex}-{digest[:16]}.{extension}"
        await run_artifact_io(_save_atomic, path, payload, digest)
        try:
            artifact = SourceArtifact(
                task_uuid=self.task_uuid,
                attempt=self.attempt,
                stage="collect",
                relative_path=path.as_posix(),
                sha256=digest,
                byte_size=len(payload),
                source_url=source_url,
                kind=kind,
                metadata_=metadata or {},
                created_at=datetime.now(timezone.utc),
            )
            async with get_db() as db:
                db.add(artifact)
        except BaseException:
            # Never expose unregistered bytes to parsers. Cleanup errors also
            # propagate, with the registration failure retained as their context.
            await run_artifact_io(_remove_published, path, digest)
            raise
        self.artifacts.append(artifact)
        return artifact


def _checked_path(path: Path) -> Path:
    root = Path("data/raw").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Artifact path escapes data/raw")
    return resolved


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_file(path: Path) -> None:
    _checked_path(path).unlink()
    _sync_directory(path.parent)


def _remove_published(path: Path, digest: str) -> None:
    try:
        _remove_file(path)
    finally:
        _discard_orphan_blob(_blob_path(digest))


def _blob_path(digest: str) -> Path:
    # Shared inode for identical payloads. The task path stays unique so each
    # source_artifacts row keeps its own name; only the bytes are shared.
    return _checked_path(Path("data/raw") / ".cas" / digest[:2] / digest)


def _usable_blob(blob: Path, size: int) -> bool:
    try:
        info = blob.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size == size


def _discard_orphan_blob(blob: Path) -> None:
    try:
        info = blob.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        blob.unlink()


def _save_atomic(path: Path, payload: bytes, digest: str | None = None) -> None:
    path = _checked_path(path)
    digest = digest or hashlib.sha256(payload).hexdigest()
    blob = _blob_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    created_blob = False
    published = False
    try:
        if _usable_blob(blob, len(payload)):
            os.link(blob, path)
            published = True
            _sync_directory(path.parent)
            return
        with tempfile.NamedTemporaryFile(
            dir=blob.parent, prefix=".artifact-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            if stream.write(payload) != len(payload):
                raise OSError("Incomplete artifact write")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Publishing the blob by hard link refuses to replace a digest that
            # already exists. A concurrent writer either wins this link or
            # adopts the existing inode below.
            os.link(temporary, blob)
            created_blob = True
        except FileExistsError:
            if not _usable_blob(blob, len(payload)):
                raise OSError(
                    "Content-addressed artifact does not match this payload"
                ) from None
        temporary.unlink()
        temporary = None
        _sync_directory(blob.parent)
        os.link(blob, path)
        published = True
        _sync_directory(path.parent)
    except BaseException:
        if published:
            _remove_file(path)
        if created_blob:
            _discard_orphan_blob(blob)
        raise
    finally:
        if temporary is not None:
            temporary.unlink()


def read_artifact(artifact) -> bytes:
    path = _checked_path(Path(artifact.relative_path))
    payload = path.read_bytes()
    if (
        len(payload) != artifact.byte_size
        or hashlib.sha256(payload).hexdigest() != artifact.sha256
    ):
        raise ValueError(f"Artifact integrity check failed: {path}")
    return payload
