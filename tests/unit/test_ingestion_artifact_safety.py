"""Artifact publication, registration, cancellation and confinement contracts."""

import asyncio
import hashlib
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.domain.collection import SourceArtifact
from src.services.ingestion import artifacts


@pytest_asyncio.fixture
async def artifact_db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(SourceArtifact.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_maker", maker)
    yield maker
    await engine.dispose()


def writer(task_uuid=None):
    return artifacts.ArtifactWriter(task_uuid or str(uuid4()), 1, "surveillance")


async def write(target, payload=b"original bytes"):
    return await target.write(
        payload, source_url="https://fixture.test/data", kind="csv_page"
    )


async def records(maker):
    async with maker() as session:
        return list((await session.scalars(select(SourceArtifact))).all())


def files():
    return [path for path in Path("data/raw").rglob("*") if path.is_file()]


async def test_concurrent_identical_writes_are_unique_and_serialized(
    artifact_db, monkeypatch
):
    target = writer()
    original = artifacts.get_db
    active = 0
    maximum = 0

    @asynccontextmanager
    async def tracked_db():
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        try:
            await asyncio.sleep(0)
            async with original() as session:
                yield session
        finally:
            active -= 1

    monkeypatch.setattr(artifacts, "get_db", tracked_db)
    saved = await asyncio.gather(*(write(target) for _ in range(24)))
    assert maximum == 1
    assert len({item.relative_path for item in saved}) == 24
    assert (
        len(await records(artifact_db)) == len(target.artifacts) == len(files()) == 24
    )
    assert all(artifacts.read_artifact(item) == b"original bytes" for item in saved)


async def test_separate_writers_for_same_attempt_do_not_collide(artifact_db):
    task_uuid = str(uuid4())
    saved = await asyncio.gather(*(write(writer(task_uuid)) for _ in range(8)))
    assert (
        len({item.relative_path for item in saved})
        == len(await records(artifact_db))
        == 8
    )


async def test_file_is_complete_and_synced_before_atomic_publication(
    artifact_db, monkeypatch
):
    original_link = artifacts.os.link
    original_fsync = artifacts.os.fsync
    synced = []
    publications = []

    def sync(descriptor):
        synced.append(descriptor)
        original_fsync(descriptor)

    def publish(temporary, destination):
        assert synced
        assert not destination.exists()
        assert temporary.read_bytes() == b"original bytes"
        original_link(temporary, destination)
        assert destination.read_bytes() == b"original bytes"
        publications.append(destination)

    monkeypatch.setattr(artifacts.os, "fsync", sync)
    monkeypatch.setattr(artifacts.os, "link", publish)
    saved = await write(writer())
    assert publications == [Path(saved.relative_path).resolve()]
    assert len(synced) == 2  # file, then containing directory
    assert not list(Path("data/raw").rglob("*.tmp"))


@pytest.mark.parametrize("failure", ["file_sync", "publish", "directory_sync"])
async def test_disk_failures_remove_partial_and_published_files(
    artifact_db, monkeypatch, failure
):
    target = writer()

    def fail(*args):
        raise OSError("disk failure")

    if failure == "file_sync":
        monkeypatch.setattr(artifacts.os, "fsync", fail)
    elif failure == "publish":
        monkeypatch.setattr(artifacts.os, "link", fail)
    else:
        monkeypatch.setattr(artifacts, "_sync_directory", fail)
    with pytest.raises(OSError, match="disk failure"):
        await write(target)
    assert files() == target.artifacts == []
    assert await records(artifact_db) == []


async def test_registration_failure_removes_file_and_does_not_poison_retry(
    artifact_db, monkeypatch
):
    target = writer()
    original = artifacts.get_db

    @asynccontextmanager
    async def fail_commit():
        async with original() as session:
            yield session
            raise RuntimeError("registration failed")

    monkeypatch.setattr(artifacts, "get_db", fail_commit)
    with pytest.raises(RuntimeError, match="registration failed"):
        await write(target)
    assert files() == target.artifacts == []
    assert await records(artifact_db) == []
    monkeypatch.setattr(artifacts, "get_db", original)
    assert artifacts.read_artifact(await write(target)) == b"original bytes"


@pytest.mark.parametrize("short_write", [False, True])
async def test_partial_write_failure_removes_temporary_file(
    artifact_db, monkeypatch, short_write
):
    original = artifacts.tempfile.NamedTemporaryFile

    class PartialStream:
        def __init__(self, **kwargs):
            self.stream = original(**kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, payload):
            count = self.stream.write(payload[:3])
            if short_write:
                return count
            raise OSError("partial write failed")

    monkeypatch.setattr(artifacts.tempfile, "NamedTemporaryFile", PartialStream)
    target = writer()
    with pytest.raises(OSError, match="Incomplete artifact write|partial write failed"):
        await write(target)
    assert target.artifacts == files() == await records(artifact_db) == []


async def test_cleanup_failure_surfaces_with_registration_failure(
    artifact_db, monkeypatch
):
    @asynccontextmanager
    async def fail_commit():
        raise RuntimeError("registration failed")
        yield

    def fail_cleanup(path):
        raise OSError("cleanup failed")

    monkeypatch.setattr(artifacts, "get_db", fail_commit)
    monkeypatch.setattr(artifacts, "_remove_file", fail_cleanup)
    target = writer()
    with pytest.raises(OSError, match="cleanup failed") as caught:
        await write(target)
    assert isinstance(caught.value.__context__, RuntimeError)
    assert str(caught.value.__context__) == "registration failed"
    assert target.artifacts == []
    assert await records(artifact_db) == []


async def test_existing_artifact_cannot_be_overwritten_or_deleted(
    artifact_db, monkeypatch
):
    monkeypatch.setattr(artifacts, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    task_uuid = str(uuid4())
    saved = await write(writer(task_uuid))
    with pytest.raises(FileExistsError):
        await write(writer(task_uuid))
    assert artifacts.read_artifact(saved) == b"original bytes"
    assert len(files()) == len(await records(artifact_db)) == 1


@pytest.mark.parametrize("fail_registration", [False, True])
async def test_cancellation_settles_inflight_file_and_registration(
    artifact_db, monkeypatch, fail_registration
):
    target = writer()
    entered = threading.Event()
    release = threading.Event()
    original_save = artifacts._save_atomic

    def blocked_save(path, payload):
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release file writer")
        original_save(path, payload)

    @asynccontextmanager
    async def fail_commit():
        raise RuntimeError("registration failed during cancellation")
        yield

    monkeypatch.setattr(artifacts, "_save_atomic", blocked_save)
    if fail_registration:
        monkeypatch.setattr(artifacts, "get_db", fail_commit)
    operation = asyncio.create_task(write(target))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(2):
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
    finally:
        release.set()
    expected = RuntimeError if fail_registration else asyncio.CancelledError
    with pytest.raises(expected):
        await asyncio.wait_for(operation, timeout=5)
    count = 0 if fail_registration else 1
    assert (
        len(files())
        == len(await records(artifact_db))
        == len(target.artifacts)
        == count
    )


async def test_cancelling_a_queued_write_does_not_persist_it(artifact_db):
    target = writer()
    async with target._lock:
        operation = asyncio.create_task(write(target))
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert target.artifacts == await records(artifact_db) == files() == []


async def test_cancellation_during_registration_waits_for_commit(
    artifact_db, monkeypatch
):
    target = writer()
    original = artifacts.get_db
    entered = asyncio.Event()
    release = asyncio.Event()

    @asynccontextmanager
    async def delayed_commit():
        async with original() as session:
            yield session
            entered.set()
            await release.wait()

    monkeypatch.setattr(artifacts, "get_db", delayed_commit)
    operation = asyncio.create_task(write(target))
    await asyncio.wait_for(entered.wait(), timeout=5)
    operation.cancel()
    await asyncio.sleep(0)
    try:
        assert not operation.done()
        assert len(files()) == 1
        assert target.artifacts == await records(artifact_db) == []
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=5)
    assert len(target.artifacts) == len(await records(artifact_db)) == 1


@pytest.mark.parametrize("attempt", ["../../escape", "1", 0, -1, True, 1.5])
def test_attempt_cannot_choose_a_filesystem_path(attempt):
    with pytest.raises(ValueError, match="positive integer"):
        artifacts.ArtifactWriter(str(uuid4()), attempt, "surveillance")


@pytest.mark.parametrize("scope", ["../outside", "/absolute", "", "two/parts"])
def test_scope_cannot_choose_a_filesystem_path(scope):
    with pytest.raises(ValueError, match="scope"):
        artifacts.ArtifactWriter(str(uuid4()), 1, scope)


async def test_symlinked_scope_cannot_escape_raw_root(artifact_db, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = Path("data/raw")
    root.mkdir(parents=True)
    (root / "surveillance").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes data/raw"):
        await write(writer())
    assert list(outside.iterdir()) == []
    assert await records(artifact_db) == []


@pytest.mark.parametrize("changed", [b"changed bytes!", b"short"])
async def test_reader_rejects_tampered_bytes(artifact_db, changed):
    saved = await write(writer())
    Path(saved.relative_path).write_bytes(changed)
    with pytest.raises(ValueError, match="integrity"):
        artifacts.read_artifact(saved)


def test_reader_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"source")
    saved = SimpleNamespace(
        relative_path=str(outside),
        byte_size=6,
        sha256=hashlib.sha256(b"source").hexdigest(),
    )
    with pytest.raises(ValueError, match="escapes data/raw"):
        artifacts.read_artifact(saved)


async def test_writer_rejects_another_event_loop(artifact_db):
    target = writer()
    await write(target)

    def foreign_loop():
        return asyncio.run(write(target))

    with pytest.raises(RuntimeError, match="owning event loop"):
        await asyncio.to_thread(foreign_loop)
    assert len(await records(artifact_db)) == 1
