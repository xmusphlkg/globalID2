from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import LiteratureSettings
from src.domain import Base, LiteratureIngestRun
from src.literature import pipeline as pipeline_module
from src.literature import replay
from src.literature.pipeline import LiteraturePipeline

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def window(issns=None):
    return replay.ReplayWindow.parse({
        "origin_run_uuid": "failed-origin",
        "since": START.isoformat(), "until": END.isoformat(),
        "issns": issns or ["0028-0836"],
    })


def test_replay_plan_uses_failed_journals_and_ignores_safe_retry_runs():
    cp = {
        "from_indexed_at": START.isoformat(),
        "requested_through_indexed_at": END.isoformat(),
        "journals": [{"issn": "0028-0836", "error": "ConnectError"}, {"issn": "0036-8075", "error": None}],
    }
    rows = [SimpleNamespace(
        status="completed", source="crossref+openalex", run_uuid="failed-origin",
        checkpoint=cp,
    ), SimpleNamespace(
        status="completed", source="crossref", run_uuid="safe-retry",
        checkpoint={**cp, "watermark_held_for_errors": True},
    )]
    assert replay.failed_journal_windows(rows) == [window()]
    assert window(["0036-8075", "0028-0836"]).key == window(["0028-0836", "0036-8075", "0028-0836"]).key


def test_replay_scope_rejects_invalid_window_and_unknown_registry_journal():
    spec = window().as_dict()
    with pytest.raises(ValueError):
        replay.ReplayWindow.parse({**spec, "until": START.isoformat()})
    with pytest.raises(ValueError):
        replay.ReplayWindow.parse({**spec, "issns": "0028-0836"})
    pipeline = replay.LiteratureReplayPipeline(LiteratureSettings(), window(["unknown"]))
    with pytest.raises(ValueError, match="outside the configured registry"):
        pipeline._collection_journals()


def test_replay_disables_other_collectors_without_mutating_normal_settings():
    config = LiteratureSettings(pubmed_enabled=True, controlled_discovery_enabled=True, publisher_rss_enabled=True)
    pipeline = replay.LiteratureReplayPipeline(config, window())
    assert config.pubmed_enabled and config.controlled_discovery_enabled and config.publisher_rss_enabled
    assert not pipeline.config.pubmed_enabled
    assert not pipeline.config.controlled_discovery_enabled
    assert not pipeline.config.publisher_rss_enabled
    assert pipeline._source_label() == replay.REPLAY_SOURCE
    assert pipeline._collection_until(END + timedelta(days=2)) == END
    assert [j["issn"] for j in pipeline._collection_journals()] == ["0028-0836"]


async def test_replay_checkpoints_never_rewind_live_watermark_or_use_other_replays(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=[LiteratureIngestRun.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with factory() as db:
            yield db
            await db.commit()

    monkeypatch.setattr(pipeline_module, "get_db", database)
    monkeypatch.setattr(replay, "get_db", database)
    scope = window()
    config = LiteratureSettings()
    try:
        async with database() as db:
            db.add(LiteratureIngestRun(
                id=1, run_uuid="live", source="crossref", status="completed",
                started_at=END + timedelta(days=1), completed_at=END + timedelta(days=1),
                through_indexed_at=END + timedelta(days=1), checkpoint={"strategy": "live"}, counts={},
            ))
            db.add(LiteratureIngestRun(
                id=2, run_uuid="replay-page", source=replay.REPLAY_SOURCE, status="completed",
                started_at=END + timedelta(days=2), completed_at=END + timedelta(days=2),
                through_indexed_at=START + timedelta(minutes=30),
                checkpoint={
                    "replay_key": scope.key, "truncated": True,
                    "next_from_indexed_at": (START + timedelta(minutes=30)).isoformat(),
                    "resume_after": {"indexed_at": (START + timedelta(minutes=30)).isoformat(), "record_ids": ["doi:boundary"]},
                }, counts={},
            ))
        normal = LiteraturePipeline(config)
        start, _ = await normal._resolve_start(END, None)
        assert start == END + timedelta(days=1)
        recovery = replay.LiteratureReplayPipeline(config, scope)
        start, resume = await recovery._resolve_start(END, None)
        assert start == START + timedelta(minutes=30)
        assert resume["record_ids"] == ["doi:boundary"]
        other = replay.LiteratureReplayPipeline(config, window(["0036-8075"]))
        assert await other._resolve_start(END, None) == (START, None)
        await recovery._finish_run("replay-page", "completed", checkpoint={
            "through_indexed_at": END.isoformat(), "truncated": False,
        }, through_indexed_at=END)
        async with database() as db:
            stored = (await db.execute(select(LiteratureIngestRun).where(LiteratureIngestRun.run_uuid == "replay-page"))).scalar_one()
            assert stored.source == replay.REPLAY_SOURCE
            assert stored.checkpoint["replay_key"] == scope.key
        latest = await LiteraturePipeline(config)._resolve_start(END, None)
        assert latest == (END + timedelta(days=1), None)
    finally:
        await engine.dispose()


async def test_replay_worker_operation_dispatches_to_isolated_pipeline(monkeypatch):
    from src.services.model_operations import execute_model_operation
    operation = AsyncMock(return_value={"replay_complete": True})
    monkeypatch.setattr(replay, "execute_replay", operation)
    arguments = window().as_dict()
    assert await execute_model_operation("literature_replay", arguments) == {"replay_complete": True}
    operation.assert_awaited_once_with(arguments)


def test_replay_window_identity_is_canonical_across_timezones():
    scope = window()
    shifted = replay.ReplayWindow.parse({
        **scope.as_dict(),
        "since": START.astimezone(timezone(timedelta(hours=8))).isoformat(),
        "until": END.astimezone(timezone(timedelta(hours=8))).isoformat(),
    })
    assert scope == shifted
    assert scope.key == shifted.key


async def test_replay_ignores_generic_task_source_and_since_overrides(monkeypatch):
    from src.domain import Task
    from src.literature.collection_sources import selected_sources, source_plan

    pipeline = replay.LiteratureReplayPipeline(LiteratureSettings(pubmed_enabled=True), window())
    task = Task(task_uuid="replay-task", input_data={"sources": ["pubmed"], "since": END.isoformat()})
    execute = AsyncMock(return_value={"inserted": 0})
    monkeypatch.setattr(LiteraturePipeline, "execute", execute)
    assert await pipeline.execute(task) == {"inserted": 0}
    scoped = execute.await_args.args[0]
    assert scoped.task_uuid == task.task_uuid
    assert scoped.input_data == {}
    assert task.input_data == {"sources": ["pubmed"], "since": END.isoformat()}
    assert source_plan(selected_sources(scoped), pipeline.config)["crossref"] is True
    assert not any(enabled for name, enabled in source_plan(selected_sources(scoped), pipeline.config).items() if name != "crossref")


async def test_replay_initial_audit_contains_scope_before_collection(monkeypatch):
    from src.domain import Task
    from src.literature import source_log

    rows = []
    database_session = SimpleNamespace(add=rows.append, commit=AsyncMock())

    @asynccontextmanager
    async def database():
        yield database_session

    log = AsyncMock()
    monkeypatch.setattr(replay, "get_db", database)
    monkeypatch.setattr(source_log, "log_literature_event", log)
    pipeline = replay.LiteratureReplayPipeline(LiteratureSettings(), window())
    await pipeline._create_run("attempt", START, END, task=Task(task_uuid="owner", input_data={"sources": ["pubmed"]}))
    assert len(rows) == 1
    assert rows[0].source == replay.REPLAY_SOURCE
    assert rows[0].status == "running"
    assert rows[0].task_uuid == "owner"
    assert rows[0].checkpoint["replay_key"] == pipeline.window.key
    assert rows[0].checkpoint["replay_window"] == pipeline.window.as_dict()
    database_session.commit.assert_awaited_once()
    assert log.await_args.kwargs["metadata"]["replay_key"] == pipeline.window.key


@pytest.mark.parametrize("complete", [False, True])
async def test_replay_worker_completed_window_is_a_noop(monkeypatch, complete):
    from src.ai import work_queue
    from src.core import config as config_module
    from src.domain import Task

    task = Task(task_uuid="replay-parent", input_data={})
    result = SimpleNamespace(scalar_one=lambda: task)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    @asynccontextmanager
    async def database():
        yield session

    monkeypatch.setattr(replay, "get_db", database)
    monkeypatch.setattr(work_queue, "current_parent", lambda: SimpleNamespace(task_uuid="replay-parent"))
    monkeypatch.setattr(config_module, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings(enabled=True)))
    checkpoint = {
        "through_indexed_at": END.isoformat(), "truncated": not complete, "source_errors": 0,
    }
    monkeypatch.setattr(replay.LiteratureReplayPipeline, "_load_latest_completed_checkpoint", AsyncMock(return_value={"checkpoint": checkpoint}))
    execute = AsyncMock(return_value={"source_truncated": 1, "inserted": 1})
    monkeypatch.setattr(replay.LiteratureReplayPipeline, "execute", execute)
    observed = await replay.execute_replay(window().as_dict())
    assert observed["replay_key"] == window().key
    if complete:
        assert observed["status"] == "already_completed"
        assert observed["replay_complete"] is True
        execute.assert_not_awaited()
    else:
        execute.assert_awaited_once_with(task)
        assert observed["replay_complete"] is False
