"""Capture thread boundaries and raw-byte-before-parser ordering."""

import asyncio
import hashlib
import sqlite3
from contextlib import asynccontextmanager
from contextvars import copy_context
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core import database
from src.domain.collection import SourceArtifact
from src.services.ingestion import artifacts, capture


@pytest_asyncio.fixture
async def capture_db(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "capture.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SourceArtifact.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_maker", maker)
    yield maker, path
    await engine.dispose()


async def records(capture_db):
    async with capture_db[0]() as session:
        return list((await session.scalars(select(SourceArtifact))).all())


@pytest.mark.parametrize("blocking", [False, True])
async def test_parser_input_is_reread_only_after_hash_registration(
    capture_db, monkeypatch, blocking
):
    raw = b"\x00unchanged source bytes\xff"
    original = capture.read_artifact
    rereads = []

    def read_registered(artifact):
        with sqlite3.connect(capture_db[1]) as connection:
            registered = connection.execute(
                "SELECT sha256, byte_size FROM source_artifacts WHERE relative_path = ?",
                (artifact.relative_path,),
            ).fetchone()
        assert registered == (hashlib.sha256(raw).hexdigest(), len(raw))
        assert Path(artifact.relative_path).read_bytes() == raw
        rereads.append(artifact.relative_path)
        return original(artifact)

    monkeypatch.setattr(capture, "read_artifact", read_registered)
    with capture.capture_sources(str(uuid4()), 1, "surveillance"):
        if blocking:
            output = await asyncio.to_thread(
                capture.capture_sync, raw, "https://fixture.test/data"
            )
        else:
            output = await capture.capture_async(raw, "https://fixture.test/data")
        assert output == raw
        assert len(rereads) == 1
    assert len(await records(capture_db)) == 1


async def test_mixed_async_and_thread_captures_are_unique(capture_db):
    with capture.capture_sources(str(uuid4()), 1, "surveillance") as bound:
        results = await asyncio.gather(
            *(
                capture.capture_async(b"same", "https://fixture.test/data.csv")
                for _ in range(8)
            ),
            *(
                asyncio.to_thread(
                    capture.capture_sync, b"same", "https://fixture.test/data.csv"
                )
                for _ in range(8)
            ),
        )
    assert results == [b"same"] * 16
    assert len({item.relative_path for item in bound.writer.artifacts}) == 16
    assert len(await records(capture_db)) == 16


async def test_capture_sync_rejects_owning_event_loop_without_scheduling(
    capture_db, monkeypatch
):
    def forbidden(*args):
        pytest.fail("capture_sync scheduled a coroutine on its blocked event loop")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", forbidden)
    with (
        pytest.raises(
            RuntimeError, match="Source artifact persistence failed"
        ) as caught,
        capture.capture_sources(str(uuid4()), 1, "literature"),
        pytest.raises(RuntimeError, match="capture_sync.*event-loop thread"),
    ):
        capture.capture_sync(b"raw", "https://fixture.test")
    assert "capture_async" in str(caught.value.__cause__)
    assert await records(capture_db) == []


@pytest.mark.parametrize("blocking", [False, True])
async def test_capture_rejects_another_event_loop(capture_db, blocking):
    async def foreign_capture():
        if blocking:
            return capture.capture_sync(b"raw", "https://fixture.test")
        return await capture.capture_async(b"raw", "https://fixture.test")

    with (
        pytest.raises(RuntimeError, match="Source artifact persistence failed"),
        capture.capture_sources(str(uuid4()), 1, "literature"),
    ):
        expected = "event-loop thread" if blocking else "owning event loop"
        with pytest.raises(RuntimeError, match=expected):
            await asyncio.to_thread(lambda: asyncio.run(foreign_capture()))
    assert await records(capture_db) == []


@pytest.mark.parametrize("closed", [False, True])
def test_capture_sync_rejects_stopped_or_closed_loop(tmp_path, monkeypatch, closed):
    monkeypatch.chdir(tmp_path)
    loop = asyncio.new_event_loop()

    async def bind():
        return capture.SourceCapture(str(uuid4()), 1, "literature")

    bound = loop.run_until_complete(bind())
    if closed:
        loop.close()
    token = capture._current.set(bound)
    try:
        with pytest.raises(RuntimeError, match="owning event loop is not running"):
            capture.capture_sync(b"raw", "https://fixture.test")
        with pytest.raises(RuntimeError, match="Source artifact persistence failed"):
            bound.verify()
    finally:
        capture._current.reset(token)
        loop.close()


async def test_submission_failure_closes_coroutine_and_is_remembered(
    capture_db, monkeypatch
):
    submitted = []

    def fail_submit(coroutine, loop):
        submitted.append(coroutine)
        raise RuntimeError("loop stopped during submission")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_submit)
    with (
        pytest.raises(RuntimeError, match="Source artifact persistence failed"),
        capture.capture_sources(str(uuid4()), 1, "literature"),
        pytest.raises(RuntimeError, match="loop stopped during submission"),
    ):
        await asyncio.to_thread(capture.capture_sync, b"raw", "https://fixture.test")
    assert submitted[0].cr_frame is None
    assert await records(capture_db) == []


@pytest.mark.parametrize("blocking", [False, True])
async def test_swallowed_persistence_failure_still_fails_capture_scope(
    capture_db, monkeypatch, blocking
):
    @asynccontextmanager
    async def fail_commit():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(artifacts, "get_db", fail_commit)
    with (
        pytest.raises(
            RuntimeError, match="Source artifact persistence failed"
        ) as caught,
        capture.capture_sources(str(uuid4()), 1, "surveillance") as bound,
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        if blocking:
            await asyncio.to_thread(
                capture.capture_sync, b"raw", "https://fixture.test"
            )
        else:
            await capture.capture_async(b"raw", "https://fixture.test")
    assert str(caught.value.__cause__) == "database unavailable"
    assert bound.writer.artifacts == await records(capture_db) == []
    assert not [path for path in Path("data/raw").rglob("*") if path.is_file()]
    assert not capture.is_capturing()


async def test_integrity_failure_is_not_hidden_by_later_success(
    capture_db, monkeypatch
):
    original = capture.read_artifact

    def tampered(artifact):
        Path(artifact.relative_path).write_bytes(b"tampered")
        return original(artifact)

    with (
        pytest.raises(
            RuntimeError, match="Source artifact persistence failed"
        ) as caught,
        capture.capture_sources(str(uuid4()), 1, "surveillance"),
    ):
        monkeypatch.setattr(capture, "read_artifact", tampered)
        with pytest.raises(ValueError, match="integrity"):
            await capture.capture_async(b"original", "https://fixture.test")
        monkeypatch.setattr(capture, "read_artifact", original)
        assert await capture.capture_async(b"next", "https://fixture.test") == b"next"
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("stage", ["organize", "store"])
async def test_capture_rejects_source_bytes_after_collection(capture_db, stage):
    with (
        pytest.raises(RuntimeError, match="Source artifact persistence failed"),
        capture.capture_sources(str(uuid4()), 1, "surveillance") as bound,
    ):
        bound.stage = stage
        with pytest.raises(RuntimeError, match=f"forbidden during {stage}"):
            await capture.capture_async(b"raw", "https://fixture.test")
    assert await records(capture_db) == []


async def test_capture_writer_reuses_runner_owned_writer_and_redacts_url(capture_db):
    writer = artifacts.ArtifactWriter(str(uuid4()), 2, "us")
    with capture.capture_writer(writer):
        assert (
            await capture.capture_async(
                b"raw",
                "https://fixture.test/data.csv?year=2025&TOKEN=secret&api_key=secret#fragment",
            )
            == b"raw"
        )
    [saved] = writer.artifacts
    assert saved.source_url == "https://fixture.test/data.csv?year=2025"
    assert saved.kind == "csv_page"
    assert saved.attempt == 2
    assert saved.relative_path.startswith(f"data/raw/us/{writer.task_uuid}/2/")


async def test_nested_scopes_restore_context_and_unbound_calls_pass_through(capture_db):
    assert capture.capture_sync(b"outside", "url") == b"outside"
    assert await capture.capture_async(b"outside", "url") == b"outside"
    with capture.capture_sources(str(uuid4()), 1, "knowledge") as outer:
        with capture.capture_sources(str(uuid4()), 1, "literature") as inner:
            await asyncio.to_thread(
                capture.capture_sync, b"inner", "https://fixture.test"
            )
        await capture.capture_async(b"outer", "https://fixture.test")
    assert [item.kind for item in inner.writer.artifacts] == ["literature_payload"]
    assert [item.kind for item in outer.writer.artifacts] == ["knowledge_payload"]
    assert not capture.is_capturing()


async def test_explicit_executor_preserves_capture_with_copy_context(capture_db):
    with capture.capture_sources(str(uuid4()), 1, "surveillance") as bound:
        context = copy_context()
        result = await asyncio.get_running_loop().run_in_executor(
            None, context.run, capture.capture_sync, b"executor", "https://fixture.test"
        )
    assert result == b"executor"
    assert len(bound.writer.artifacts) == 1
