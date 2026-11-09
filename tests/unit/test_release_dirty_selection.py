"""Release scopes and acknowledgements use committed dirty rows, never task history."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Country, DataReleaseJob, Task, TaskPriority, TaskStatus, TaskType
from src.domain.collection import PublishDirty
from src.services import data_release_service as module
from src.services.data_release import projection
from src.services.data_release_service import DataReleaseJobConfig, DataReleaseService
from tests.unit.publication_sqlite import create_publication_tables


@pytest_asyncio.fixture
async def release_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'release.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Country.__table__,
                    Task.__table__,
                    DataReleaseJob.__table__,
                    PublishDirty.__table__,
                ],
            )
        )
        await connection.run_sync(create_publication_tables)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as session:
            yield session

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(module.task_manager, "_broadcast", AsyncMock())
    service = DataReleaseService()
    monkeypatch.setattr(service, "_download_repo_branch", lambda _: "main")
    monkeypatch.setattr(service, "_cloudflare_project_name", lambda _: "globalid")
    monkeypatch.setattr(
        service, "_download_repo_url", lambda: "https://example.test/downloads.git"
    )
    monkeypatch.setattr(
        service, "_download_repo_raw_base", lambda _: "https://example.test/raw/main"
    )
    monkeypatch.setattr(
        service,
        "_raw_archive_runtime",
        lambda: SimpleNamespace(enabled=False, branch="main", repo_url=""),
    )
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC"))
    monkeypatch.setattr(service, "_automatic_retry_settings", lambda: (0, 0, 0))
    async with maker() as session:
        session.add_all(
            [
                Country(code="US", name="US", name_en="US"),
                Country(code="JP", name="JP", name_en="JP"),
                DataReleaseJob(job_id="site-release", name="Site Release"),
            ]
        )
        await session.commit()
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_sql(_connection, _cursor, statement, *_args):
        statements.append(statement)

    yield maker, service, statements
    await engine.dispose()


async def enqueue(maker, service, *, full=False):
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site Release"),
        manual=True,
        trigger="test",
        trigger_task_uuid=None,
        full=full,
    )
    assert result["status"] == "queued"
    async with maker() as session:
        return (
            await session.execute(
                select(Task).where(Task.task_uuid == result["task_uuid"])
            )
        ).scalar_one()


@pytest.mark.parametrize("has_global", [False, True])
async def test_situation_json_leaves_non_global_markers_for_operator_review(release_db, has_global):
    maker, service, _ = release_db
    async with maker() as session:
        invalid = PublishDirty(line="situation", scope_key="legacy-source",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))
        valid = PublishDirty(line="situation", scope_key="*",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))
        session.add(invalid)
        if has_global:
            session.add(valid)
        await session.commit()
        invalid_id, valid_id = invalid.id, valid.id
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site Release"),
        manual=False, trigger="upstream_completion", trigger_task_uuid=None,
        data_only=True, data_line="situation")
    if has_global:
        async with maker() as session:
            task = (await session.execute(select(Task).where(
                Task.task_uuid == result["task_uuid"])) ).scalar_one()
            assert task.input_data["publish_dirty_ids"] == [valid_id]
    else:
        assert result["reason"] == "no_publishable_changes"
    async with maker() as session:
        assert (await session.get(PublishDirty, invalid_id)).published_at is None


@pytest.mark.parametrize("json_enabled,expected_lines", [
    (False, {"surveillance", "literature", "situation"}),
    (True, {"literature", "situation"}),
])
@pytest.mark.parametrize("full", [False, True])
async def test_static_release_selects_only_its_channel_when_json_enabled(release_db, monkeypatch, json_enabled, expected_lines, full):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", surveillance_json_enabled=json_enabled))
    async with maker() as session:
        rows = [PublishDirty(line=line, scope_key="US" if line == "surveillance" else "*", source_task_uuid="source", marked_at=datetime.now(timezone.utc))
                for line in ("surveillance", "literature", "situation")]
        session.add_all(rows)
        await session.commit()
        ids_by_line = {row.line: row.id for row in rows}
    task = await enqueue(maker, service, full=full)
    assert set(task.input_data["publish_dirty_ids"]) == {ids_by_line[line] for line in expected_lines}
    assert task.input_data["content_only"] is json_enabled


async def test_json_release_selects_surveillance_and_runs_beside_static_lane(release_db):
    maker, service, _ = release_db
    async with maker() as session:
        surveillance = PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source", marked_at=datetime.now(timezone.utc))
        session.add_all([surveillance, PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)),
            Task(task_uuid="site-running", task_type=TaskType.EXPORT_DATA,
                 task_name="Site", status=TaskStatus.RUNNING, input_data={"content_only": True})])
        await session.commit()
        marker_id = surveillance.id
    job = DataReleaseJobConfig(job_id="site-release", name="Site Release")
    first = await service._enqueue_release_task(job, manual=True, trigger="test", trigger_task_uuid=None, data_only=True)
    assert first["status"] == "queued"
    async with maker() as session:
        task = (await session.execute(select(Task).where(Task.task_uuid == first["task_uuid"]))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == [marker_id]
        assert task.input_data["data_only"] is True
        assert task.input_data["content_only"] is False
        assert task.metadata_["data_only"] is True
        assert task.input_data["incremental_country_codes"] == ["US"]
        assert not task.input_data["include_cloudflare_deploy"]
        assert not task.input_data["include_git_push"]
        assert not task.input_data["raw_archive_enabled"]
    second = await service._enqueue_release_task(job, manual=True, trigger="test", trigger_task_uuid=None, data_only=True)
    assert second["status"] == "skipped"
    assert second["task_uuid"] == first["task_uuid"]


@pytest.mark.parametrize("enabled", [False, True])
async def test_situation_gate_freezes_selection_and_static_contract(release_db, monkeypatch, enabled):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC",
        surveillance_json_enabled=True, situation_json_enabled=enabled))
    async with maker() as session:
        rows = [PublishDirty(line=line, scope_key="US" if line == "surveillance" else "*",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))
            for line in ("surveillance", "situation", "literature")]
        session.add_all(rows)
        await session.commit()
        ids = {row.line: row.id for row in rows}
    task = await enqueue(maker, service, full=True)
    assert task.input_data["situation_detached"] is enabled
    assert set(task.input_data["publish_dirty_ids"]) == {ids["literature"]} | (set() if enabled else {ids["situation"]})


async def test_situation_json_selects_exact_own_line_without_surveillance_scopes(release_db):
    maker, service, _ = release_db
    async with maker() as session:
        rows = [PublishDirty(line=line, scope_key="US" if line == "surveillance" else "*",
            source_task_uuid="source", marked_at=datetime.now(timezone.utc))
            for line in ("surveillance", "situation", "literature")]
        session.add_all(rows)
        session.add(Task(task_uuid="site-safe", task_type=TaskType.EXPORT_DATA, task_name="Site",
            status=TaskStatus.RETRYING, input_data={"content_only": True, "situation_detached": True}))
        await session.commit()
        selected_id = rows[1].id
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=False, trigger="upstream_completion", trigger_task_uuid=None, data_only=True, data_line="situation")
    assert result["status"] == "queued"
    async with maker() as session:
        task = (await session.execute(select(Task).where(Task.task_uuid == result["task_uuid"]))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == [selected_id]
        assert task.input_data["data_line"] == task.metadata_["data_line"] == "situation"
        assert task.input_data["incremental_country_codes"] == []
        assert task.input_data["continuation_dirty_ids"] == []


@pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING])
async def test_situation_waits_for_legacy_content_task_owning_its_dirty(release_db, status):
    maker, service, _ = release_db
    async with maker() as session:
        session.add_all([PublishDirty(line="situation", scope_key="*", source_task_uuid="source",
            marked_at=datetime.now(timezone.utc)), Task(task_uuid="old-content", task_type=TaskType.EXPORT_DATA,
            task_name="Old content", status=status, input_data={"content_only": True})])
        await session.commit()
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=False, trigger="upstream_completion", trigger_task_uuid=None, data_only=True, data_line="situation")
    assert result["status"] == "skipped"
    assert result["task_uuid"] == "old-content"


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING])
async def test_json_enqueue_waits_for_legacy_site_in_every_reserved_state(release_db, status):
    maker, service, _ = release_db
    async with maker() as session:
        session.add_all([
            PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source", marked_at=datetime.now(timezone.utc)),
            Task(task_uuid="legacy", task_type=TaskType.EXPORT_DATA, task_name="Legacy site",
                 status=status, input_data={}),
        ])
        await session.commit()
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True,
    )
    assert result["status"] == "skipped"
    assert result["task_uuid"] == "legacy"


@pytest.mark.parametrize("json_enabled,mode,expected", [
    (False, {}, "skipped"), (True, {}, "queued"),
    (False, {"reports_only": True}, "queued"),
    (False, {"code_only": True}, "queued"),
])
async def test_legacy_site_waits_for_json_but_frozen_safe_modes_can_queue(release_db, monkeypatch, json_enabled, mode, expected):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", surveillance_json_enabled=json_enabled))
    async with maker() as session:
        session.add_all([
            PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)),
            Task(task_uuid="json", task_type=TaskType.EXPORT_DATA, task_name="JSON",
                 status=TaskStatus.RETRYING, input_data={"data_only": True}),
        ])
        await session.commit()
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, **mode,
    )
    assert result["status"] == expected
    if expected == "queued":
        async with maker() as session:
            task = (await session.execute(select(Task).where(Task.task_uuid == result["task_uuid"]))).scalar_one()
        assert task.input_data["content_only"] is (json_enabled and not bool(mode))
        assert not task.input_data["data_only"]


async def test_empty_json_release_never_becomes_full_or_acknowledges_other_lines(release_db):
    maker, service, _ = release_db
    async with maker() as session:
        session.add(PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)))
        await session.commit()
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site Release"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True,
    )
    assert result["status"] == "skipped"
    async with maker() as session:
        assert list((await session.execute(select(Task))).scalars()) == []


async def test_json_revalidates_exact_dirty_ids_after_waiting_for_queue_reservation(release_db, monkeypatch):
    maker, service, _ = release_db
    async with maker() as session:
        marker = PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source", marked_at=datetime.now(timezone.utc))
        session.add(marker)
        await session.commit()
        marker_id = marker.id
    entries = 0

    @asynccontextmanager
    async def database():
        nonlocal entries
        entries += 1
        if entries == 2:
            async with maker() as other_publisher:
                row = await other_publisher.get(PublishDirty, marker_id)
                row.published_at = datetime.now(timezone.utc)
                await other_publisher.commit()
        async with maker() as session:
            yield session

    monkeypatch.setattr(module, "get_database", database)
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True,
    )
    assert result["reason"] == "no_pending_changes"
    async with maker() as session:
        assert list((await session.execute(select(Task))).scalars()) == []


async def test_json_reconciliation_ignores_static_schedule_and_preserves_its_state(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=False, surveillance_json_enabled=True, timezone="UTC", auto_failure_cooldown_minutes=0,
    ))
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[
        DataReleaseJobConfig(job_id="site-release", name="Site", auto_after_crawls=False, daily_time=None),
    ]))
    save = AsyncMock()
    monkeypatch.setattr(module.schedule_state_repository, "save", save)
    async with maker() as session:
        session.add(PublishDirty(line="surveillance", scope_key="US", source_task_uuid="source", marked_at=datetime.now(timezone.utc)))
        await session.commit()
    await service.reconcile_pending_surveillance()
    await service.reconcile_pending_surveillance()
    async with maker() as session:
        tasks = list((await session.execute(select(Task))).scalars())
    assert len(tasks) == 1 and tasks[0].input_data["data_only"] is True
    assert service._states == {}
    save.assert_not_awaited()


async def test_static_queue_can_start_while_json_channel_has_retry_reservation(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", surveillance_json_enabled=True))
    async with maker() as session:
        session.add_all([
            PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)),
            Task(task_uuid="data-retry", task_type=TaskType.EXPORT_DATA, task_name="JSON retry",
                 status=TaskStatus.RETRYING, input_data={"data_only": True}),
        ])
        await session.commit()
    task = await enqueue(maker, service)
    assert not task.input_data["data_only"]


async def test_json_scheduler_poll_runs_when_static_schedule_is_disabled(monkeypatch):
    import asyncio

    service = DataReleaseService()
    service._stop_event = asyncio.Event()
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=False, surveillance_json_enabled=True, poll_interval_seconds=1,
        reports_auto_publish_enabled=True,
    ))
    monkeypatch.setattr(service, "requeue_due_automatic_retries", AsyncMock())
    monkeypatch.setattr(service, "reconcile_release_continuations", AsyncMock())
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[
        DataReleaseJobConfig(job_id="site-release", name="Site", daily_time="00:00", auto_after_crawls=False),
    ]))
    monkeypatch.setattr(service, "trigger_job", AsyncMock())
    monkeypatch.setattr(service, "reconcile_pending_reports", AsyncMock())

    async def publish_data():
        service._stop_event.set()

    reconcile = AsyncMock(side_effect=publish_data)
    monkeypatch.setattr(service, "reconcile_pending_surveillance", reconcile)
    await service._run_loop()
    reconcile.assert_awaited_once_with()
    service.trigger_job.assert_not_awaited()
    service.reconcile_pending_reports.assert_not_awaited()


async def test_situation_poll_and_completion_hook_work_with_static_and_surveillance_disabled(release_db, monkeypatch):
    maker, service, _ = release_db
    service._storage_ready = True
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(enabled=False,
        situation_json_enabled=True, surveillance_json_enabled=False, timezone="UTC", auto_failure_cooldown_minutes=0))
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[DataReleaseJobConfig(job_id="site-release", name="Site")]))
    async with maker() as session:
        row = PublishDirty(line="situation", scope_key="*", source_task_uuid="situation-source", marked_at=datetime.now(timezone.utc))
        session.add(row)
        await session.commit()
    await service.maybe_trigger_after_task_completion("situation-source", TaskType.REFRESH_SITUATION_SOURCES)
    await service.reconcile_pending_situation()  # missed hook/restart poll cannot duplicate it
    async with maker() as session:
        tasks = list((await session.execute(select(Task))).scalars())
    assert len(tasks) == 1
    assert tasks[0].input_data["data_line"] == "situation"


async def test_invalid_surveillance_reconcile_does_not_stop_situation(monkeypatch):
    service = DataReleaseService()
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        surveillance_json_enabled=True, situation_json_enabled=True))
    monkeypatch.setattr(service, "reconcile_pending_surveillance", AsyncMock(side_effect=ValueError("Unknown scope")))
    monkeypatch.setattr(service, "reconcile_pending_situation", AsyncMock())
    await service._reconcile_json_channels()
    service.reconcile_pending_situation.assert_awaited_once_with()


async def test_json_channels_alternate_without_consuming_continuation(release_db, monkeypatch):
    maker, service, marker_id, parent_id = await _continuation_parent(release_db, monkeypatch, flags={"data_only": True})
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(enabled=False, surveillance_json_enabled=True,
        situation_json_enabled=True, timezone="UTC", auto_failure_cooldown_minutes=0))
    async with maker() as session:
        session.add(PublishDirty(line="situation", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)))
        await session.commit()
    await service.reconcile_release_continuations()
    await service.reconcile_pending_surveillance()
    await service.reconcile_pending_situation()
    async with maker() as session:
        tasks = list((await session.execute(select(Task).order_by(Task.id))).scalars())
        assert len(tasks) == 2
        assert tasks[1].input_data["data_line"] == "situation"
        assert not (tasks[0].metadata_ or {}).get("release_continuation")
        tasks[1].status = TaskStatus.COMPLETED
        tasks[1].completed_at = datetime.now(timezone.utc)
        await session.execute(module.PublishDirty.__table__.update().where(PublishDirty.line == "situation").values(published_at=datetime.now(timezone.utc)))
        await session.commit()
    await service.reconcile_release_continuations()
    async with maker() as session:
        child = (await session.execute(select(Task).where(Task.input_data["continuation_parent_task_uuid"].as_string() == "parent"))).scalar_one()
        assert child.input_data["publish_dirty_ids"] == [marker_id]


async def test_cooldown_is_independent_between_json_lines(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", auto_failure_cooldown_minutes=60))
    async with maker() as session:
        session.add(Task(task_uuid="situation-failure", task_type=TaskType.EXPORT_DATA, task_name="Failed",
            status=TaskStatus.FAILED, completed_at=datetime.now(timezone.utc),
            input_data={"data_only": True, "data_line": "situation"}, metadata_={"release_job_id": "site-release"}))
        await session.commit()
    assert await service._release_failure_cooldown_reason("site-release", data_only=True) is None
    assert await service._release_failure_cooldown_reason("site-release", data_only=True, data_line="situation")


async def test_blocked_surveillance_does_not_starve_situation(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC",
        surveillance_json_enabled=True, situation_json_enabled=True, auto_failure_cooldown_minutes=0))
    async with maker() as session:
        session.add_all([Country(code="IE", name="IE", name_en="IE", is_active=False),
            PublishDirty(line="surveillance", scope_key="IE", source_task_uuid="blocked", marked_at=datetime.now(timezone.utc)),
            Task(task_uuid="last-situation", task_type=TaskType.EXPORT_DATA, task_name="Situation",
                status=TaskStatus.COMPLETED, input_data={"data_only": True, "data_line": "situation"})])
        await session.commit()
    assert not await service._yield_json_turn("site-release", "situation")


@pytest.mark.parametrize("flags", [
    {"full": True}, {"reports_only": True}, {"code_only": True},
])
async def test_json_mode_rejects_static_modes_before_loading_jobs(flags):
    with pytest.raises(ValueError, match="mutually exclusive"):
        await DataReleaseService().trigger_job("site", manual=True, trigger="test", data_only=True, **flags)


@pytest.mark.parametrize("failed_data_only,query_data_only", [(False, True), (True, False)])
async def test_failure_cooldown_is_scoped_to_publication_channel(release_db, monkeypatch, failed_data_only, query_data_only):
    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", auto_failure_cooldown_minutes=60))
    async with maker() as session:
        session.add(Task(task_uuid="failed", task_type=TaskType.EXPORT_DATA, task_name="Failed",
            status=TaskStatus.FAILED, completed_at=datetime.now(timezone.utc),
            input_data={"data_only": failed_data_only}, metadata_={"release_job_id": "site-release"}))
        await session.commit()
    assert await service._release_failure_cooldown_reason("site-release", data_only=query_data_only) is None
    assert await service._release_failure_cooldown_reason("site-release", data_only=failed_data_only)


async def _continuation_parent(release_db, monkeypatch, *, flags=None):
    maker, service, _ = release_db
    service._storage_ready = True
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=True, timezone="UTC", batch_max_countries=1, auto_failure_cooldown_minutes=0,
    ))
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[
        DataReleaseJobConfig(job_id="site-release", name="Site Release", auto_after_crawls=False),
    ]))
    monkeypatch.setattr(module.schedule_state_repository, "save", AsyncMock())
    async with maker() as session:
        marker = PublishDirty(
            line="surveillance", scope_key="JP", source_task_uuid="crawl-1",
            marked_at=datetime.now(timezone.utc),
            change_set={"version": 1, "precise": True, "changes": []},
        )
        session.add(marker)
        await session.flush()
        parent = Task(
            task_uuid="parent", task_type=TaskType.EXPORT_DATA, task_name="First batch",
            status=TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc),
            input_data={"release_job_id": "site-release", "continuation_dirty_ids": [marker.id], **(flags or {})},
            metadata_={"release_job_id": "site-release"}, output_data={"pages_deployed": True},
        )
        session.add(parent)
        await session.commit()
        return maker, service, marker.id, parent.id


async def test_json_continuation_keeps_channel_and_ignores_running_site(release_db, monkeypatch):
    maker, service, marker_id, _parent_id = await _continuation_parent(
        release_db, monkeypatch, flags={"data_only": True},
    )
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(
        enabled=True, surveillance_json_enabled=True, timezone="UTC", auto_failure_cooldown_minutes=0,
    ))
    async with maker() as session:
        session.add(Task(task_uuid="site-busy", task_type=TaskType.EXPORT_DATA,
            task_name="Site", status=TaskStatus.RUNNING, input_data={"content_only": True}))
        await session.commit()
    await service.reconcile_release_continuations()
    async with maker() as session:
        children = list((await session.execute(select(Task).where(
            Task.input_data["continuation_parent_task_uuid"].as_string() == "parent",
        ))).scalars())
    assert len(children) == 1
    assert children[0].input_data["data_only"] is True
    assert children[0].input_data["publish_dirty_ids"] == [marker_id]


@pytest.mark.parametrize("line", ["surveillance", "situation"])
async def test_data_retry_reservation_requeues_beside_static_release(release_db, monkeypatch, line):
    from datetime import timedelta

    maker, service, _ = release_db
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(**{f"{line}_json_enabled": True}))
    monkeypatch.setattr(module.task_manager, "add_workbook_entry", AsyncMock())
    now = datetime.now(timezone.utc)
    async with maker() as session:
        session.add_all([
            Task(task_uuid="site-busy", task_type=TaskType.EXPORT_DATA, task_name="Site",
                 status=TaskStatus.RUNNING, input_data={"content_only": True, "situation_detached": True}),
            Task(task_uuid="json-retry", task_type=TaskType.EXPORT_DATA, task_name="JSON",
                 status=TaskStatus.RETRYING,
                 input_data={"data_only": True, "data_line": line, "trigger": "upstream_completion", "manual_trigger": False},
                 metadata_={"release_job_id": "site-release", "automatic_retry": {
                     "status": "scheduled", "scheduled_attempts": 1,
                     "next_attempt_at": (now - timedelta(seconds=1)).isoformat(),
                 }}),
        ])
        await session.commit()
    assert await service.requeue_due_automatic_retries(now=now) == ["json-retry"]


@pytest.mark.parametrize("line", [None, "literature", "situation", "knowledge"])
async def test_automatic_noop_skips_release_but_other_data_lines_still_publish(release_db, line):
    maker, service, _ = release_db
    if line:
        async with maker() as session:
            session.add(PublishDirty(
                line=line, scope_key="changed", source_task_uuid="upstream",
                marked_at=datetime.now(timezone.utc),
            ))
            await session.commit()
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site Release"),
        manual=False, trigger="upstream_completion", trigger_task_uuid="upstream",
    )
    async with maker() as session:
        tasks = (await session.execute(select(Task))).scalars().all()
    if line is None:
        assert result["reason"] == "no_publishable_changes"
        assert tasks == []
    else:
        assert result["status"] == "queued"
        assert len(tasks) == 1
        assert tasks[0].input_data["publish_change_count"] == 1


async def test_continuation_freezes_ids_and_does_not_absorb_new_changes(release_db, monkeypatch):
    maker, service, marker_id, parent_id = await _continuation_parent(release_db, monkeypatch)
    async with maker() as session:
        # Same country, later transaction: must wait for another release chain.
        newer = PublishDirty(line="surveillance", scope_key="JP", source_task_uuid="crawl-2",
                             marked_at=datetime.now(timezone.utc))
        session.add(newer)
        await session.commit()
        newer_id = newer.id
    await service.maybe_trigger_after_task_completion("parent", TaskType.EXPORT_DATA)
    async with maker() as session:
        tasks = (await session.execute(select(Task).order_by(Task.id))).scalars().all()
        assert len(tasks) == 2
        child = tasks[1]
        assert child.input_data["publish_dirty_ids"] == [marker_id]
        assert child.input_data["continuation_dirty_ids"] == []
        assert child.input_data["continuation_parent_task_uuid"] == "parent"
        assert child.input_data["manual_trigger"] is False
        assert (await session.get(Task, parent_id)).metadata_["release_continuation"]["task_uuid"] == child.task_uuid
        child.status = TaskStatus.COMPLETED
        child.completed_at = datetime.now(timezone.utc)
        (await session.get(PublishDirty, marker_id)).published_at = datetime.now(timezone.utc)
        await session.commit()
        child_uuid = child.task_uuid
    await service.maybe_trigger_after_task_completion(child_uuid, TaskType.EXPORT_DATA)
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 2
        assert (await session.get(PublishDirty, newer_id)).published_at is None


async def test_scheduler_recovers_missed_continuation_hook_once(release_db, monkeypatch):
    maker, service, _marker_id, _parent_id = await _continuation_parent(release_db, monkeypatch)
    await service.reconcile_release_continuations()
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 2


async def test_drained_continuation_does_not_enqueue_empty_release(release_db, monkeypatch):
    maker, service, marker_id, parent_id = await _continuation_parent(release_db, monkeypatch)
    async with maker() as session:
        (await session.get(PublishDirty, marker_id)).published_at = datetime.now(timezone.utc)
        await session.commit()
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 1
        assert (await session.get(Task, parent_id)).metadata_["release_continuation"]["status"] == "exhausted"


@pytest.mark.parametrize("flag", ["raw_archive_only", "reports_only", "code_only"])
async def test_non_data_release_never_continues_surveillance_batches(release_db, monkeypatch, flag):
    maker, service, _marker_id, _parent_id = await _continuation_parent(release_db, monkeypatch, flags={flag: True})
    await service.maybe_trigger_after_task_completion("parent", TaskType.EXPORT_DATA)
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 1


async def test_busy_continuation_waits_and_raw_archive_does_not_block_it(release_db, monkeypatch):
    maker, service, _marker_id, _parent_id = await _continuation_parent(release_db, monkeypatch)
    async with maker() as session:
        busy = Task(task_uuid="busy", task_name="Another release", task_type=TaskType.EXPORT_DATA,
                    status=TaskStatus.RUNNING, input_data={"code_only": True})
        session.add(busy)
        await session.commit()
        busy_id = busy.id
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 2
        # A raw archive may remain running while the next site batch starts.
        busy = await session.get(Task, busy_id)
        busy.input_data = {"raw_archive_only": True}
        await session.commit()
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 3


async def test_continuation_recovers_existing_failed_child_without_forking(release_db, monkeypatch):
    maker, service, _marker_id, parent_id = await _continuation_parent(release_db, monkeypatch)
    await service.reconcile_release_continuations()
    async with maker() as session:
        parent = await session.get(Task, parent_id)
        parent.metadata_ = {"release_job_id": "site-release"}
        child = (await session.execute(select(Task).where(Task.id != parent_id))).scalar_one()
        child.status = TaskStatus.FAILED
        child_uuid = child.task_uuid
        await session.commit()
    await service.reconcile_release_continuations()
    async with maker() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 2
        assert (await session.get(Task, parent_id)).metadata_["release_continuation"]["task_uuid"] == child_uuid


async def test_raw_archive_history_cannot_hide_main_release_failure(release_db):
    maker, service, _ = release_db
    async with maker() as session:
        session.add(Task(
            task_uuid="site-failed", task_name="Site", task_type=TaskType.EXPORT_DATA,
            status=TaskStatus.FAILED, completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metadata_={"release_job_id": "site-release"},
        ))
        session.add_all([
            Task(task_uuid=f"archive-{index}", task_name="Archive", task_type=TaskType.EXPORT_DATA,
                 status=TaskStatus.COMPLETED, completed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                 input_data={"raw_archive_only": True}, metadata_={"release_job_id": "site-release"})
            for index in range(105)
        ])
        await session.commit()
    latest = await service._latest_terminal_release_task("site-release")
    assert latest.task_uuid == "site-failed"
    assert not await service._release_up_to_date("site-release", datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("stored_priority", ["low", "normal", "high", "bogus"])
async def test_release_is_always_queued_at_urgent_priority(
    release_db, stored_priority
):
    maker, service, _ = release_db
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(
            job_id="site-release", name="Site Release", priority=stored_priority
        ),
        manual=True,
        trigger="test",
        trigger_task_uuid=None,
    )
    async with maker() as session:
        task = (
            await session.execute(
                select(Task).where(Task.task_uuid == result["task_uuid"])
            )
        ).scalar_one()
    assert task.priority == TaskPriority.URGENT


@pytest.mark.parametrize("has_california", [False, True])
async def test_us_only_release_then_no_dirty_release_does_not_recompute_countries(
    release_db, monkeypatch, has_california
):
    maker, service, _ = release_db
    async with maker() as session:
        if has_california:
            session.add(Country(code="US-CA", name="California", name_en="California"))
        marker = PublishDirty(
            line="surveillance",
            scope_key="US",
            source_task_uuid="collection-1",
            marked_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()
        marker_id = marker.id
    first = await enqueue(maker, service)
    data = first.input_data
    expected_codes = ["US", "US-CA"] if has_california else ["US"]
    assert data["incremental_country_codes"] == expected_codes
    assert data["publish_dirty_ids"] == [marker_id]
    published = []
    monkeypatch.setattr(
        projection,
        "_load_publish_context",
        AsyncMock(
            return_value={
                "countries_by_code": {code: {} for code in ["JP", *expected_codes]}
            }
        ),
    )

    async def publish(_session, code, _context, *, force):
        assert force
        published.append(code)

    monkeypatch.setattr(projection, "_publish_country", publish)
    async with maker() as session:
        await projection.commit_release_projection(
            session,
            task_uuid=first.task_uuid,
            country_codes=data["incremental_country_codes"],
            full=False,
            dirty_ids=data["publish_dirty_ids"],
        )
        task = await session.get(Task, first.id)
        task.status = TaskStatus.COMPLETED
        await session.commit()
    assert published == expected_codes
    async with maker() as session:
        row = await session.get(PublishDirty, marker_id)
        assert (
            row.published_task_uuid == first.task_uuid and row.published_at is not None
        )
    second = await enqueue(maker, service)
    assert second.input_data["incremental_country_codes"] == []
    assert second.input_data["publish_dirty_ids"] == []
    assert second.input_data["incremental_site"] is True
    no_publish = AsyncMock(
        side_effect=AssertionError("empty release must not project countries")
    )
    monkeypatch.setattr(projection, "_load_publish_context", no_publish)
    async with maker() as session:
        await projection.commit_release_projection(
            session,
            task_uuid=second.task_uuid,
            country_codes=[],
            full=False,
            dirty_ids=[],
        )
    no_publish.assert_not_awaited()
    command = service._generate_site_data_command(
        python_path=Path("python"),
        download_url_base="https://example.test/raw",
        incremental_country_codes=[],
        incremental_site=True,
    )
    assert "--incremental-site" in command and "--incremental-country" not in command


async def test_reports_only_release_leaves_other_dirty_scopes_pending(release_db):
    maker, service, statements = release_db
    async with maker() as session:
        session.add(PublishDirty(line="surveillance", scope_key="JP", source_task_uuid="crawl", marked_at=datetime.now(timezone.utc)))
        await session.commit()
    statements.clear()
    result = await service._enqueue_release_task(
        DataReleaseJobConfig(job_id="site-release", name="Site Release"),
        manual=True, trigger="test", trigger_task_uuid=None, reports_only=True,
    )
    async with maker() as session:
        task = (await session.execute(select(Task).where(Task.task_uuid == result["task_uuid"]))).scalar_one()
        marker = (await session.execute(select(PublishDirty))).scalar_one()
    assert task.input_data["reports_only"] is True
    assert task.input_data["publish_dirty_ids"] == []
    assert task.input_data["include_git_push"] is False
    assert task.input_data["include_cloudflare_deploy"] is False
    assert marker.published_at is None


async def test_full_release_freezes_visible_change_log_ids(release_db):
    maker, service, statements = release_db
    async with maker() as session:
        session.add(
            PublishDirty(
                line="surveillance",
                scope_key="US",
                source_task_uuid="collection-1",
                marked_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    statements.clear()
    task = await enqueue(maker, service, full=True)
    assert any("FROM publish_dirty" in statement for statement in statements)
    assert task.input_data["full"] is True
    assert task.input_data["incremental_site"] is False
    assert len(task.input_data["publish_dirty_ids"]) == 1
    assert task.input_data["publish_dirty_before"] is None
    assert task.input_data["publish_selected_at"]
    assert task.input_data["publish_change_count"] == 1
    command = service._generate_site_data_command(
        python_path=Path("python"),
        download_url_base="https://example.test/raw",
        incremental_country_codes=[],
        incremental_site=False,
    )
    assert (
        "--incremental-site" not in command and "--incremental-country" not in command
    )


async def test_full_projection_consumes_only_pre_enqueue_markers(
    release_db, monkeypatch
):
    maker, _, _ = release_db
    boundary = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
    async with maker() as session:
        session.add_all(
            [
                PublishDirty(
                    line="surveillance",
                    scope_key="US",
                    source_task_uuid="before",
                    marked_at=boundary,
                ),
                PublishDirty(
                    line="surveillance",
                    scope_key="JP",
                    source_task_uuid="after",
                    marked_at=datetime(2026, 10, 30, 11, tzinfo=timezone.utc),
                ),
                PublishDirty(
                    line="surveillance",
                    scope_key="ZZ",
                    source_task_uuid="unknown",
                    marked_at=boundary,
                ),
            ]
        )
        await session.commit()
    monkeypatch.setattr(
        projection,
        "_load_publish_context",
        AsyncMock(return_value={"countries_by_code": {"US": {}, "JP": {}}}),
    )
    publish = AsyncMock()
    monkeypatch.setattr(projection, "_publish_country", publish)
    async with maker() as session:
        await projection.commit_release_projection(
            session,
            task_uuid="release-full",
            country_codes=[],
            full=True,
            dirty_ids=[],
            marked_before=boundary.isoformat(),
        )
        await session.commit()
    assert {call.args[1] for call in publish.await_args_list} == {"US", "JP"}
    async with maker() as session:
        rows = (
            (await session.execute(select(PublishDirty).order_by(PublishDirty.id)))
            .scalars()
            .all()
        )
        assert rows[0].published_task_uuid == "release-full"
        assert rows[1].published_at is None
        assert rows[2].published_at is None


async def test_full_release_keeps_late_committed_backdated_changes_pending(release_db, monkeypatch):
    maker, service, _ = release_db
    async with maker() as session:
        first = PublishDirty(line="surveillance", scope_key="US", source_task_uuid="first", marked_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        session.add(first)
        await session.commit()
        first_id = first.id
    task = await enqueue(maker, service, full=True)
    async with maker() as session:
        late = PublishDirty(line="surveillance", scope_key="US", source_task_uuid="late", marked_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        session.add(late)
        await session.commit()
        late_id = late.id
    monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={"countries_by_code": {"US": {}}}))
    monkeypatch.setattr(projection, "_publish_country", AsyncMock())
    async with maker() as session:
        await projection.commit_release_projection(
            session, task_uuid=task.task_uuid, country_codes=[], full=True,
            dirty_ids=task.input_data["publish_dirty_ids"],
            marked_before=task.input_data["publish_dirty_before"],
        )
        await session.commit()
        assert (await session.get(PublishDirty, first_id)).published_task_uuid == task.task_uuid
        assert (await session.get(PublishDirty, late_id)).published_at is None


async def test_unknown_dirty_scope_rejects_enqueue_and_preserves_marker(release_db):
    maker, service, _ = release_db
    async with maker() as session:
        session.add(
            PublishDirty(
                line="surveillance",
                scope_key="ZZ",
                source_task_uuid="unknown",
                marked_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    with pytest.raises(ValueError, match="no country configuration: ZZ"):
        await enqueue(maker, service)
    async with maker() as session:
        assert (await session.execute(select(Task))).scalars().all() == []
        assert (
            await session.execute(select(PublishDirty))
        ).scalar_one().published_at is None


async def test_projection_cannot_acknowledge_unknown_requested_scope(
    release_db, monkeypatch
):
    maker, _, _ = release_db
    async with maker() as session:
        marker = PublishDirty(
            line="surveillance",
            scope_key="ZZ",
            source_task_uuid="unknown",
            marked_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()
        marker_id = marker.id
    monkeypatch.setattr(
        projection,
        "_load_publish_context",
        AsyncMock(return_value={"countries_by_code": {"US": {}, "JP": {}}}),
    )
    publish = AsyncMock()
    monkeypatch.setattr(projection, "_publish_country", publish)
    with pytest.raises(ValueError, match="unknown or disabled country scopes: ZZ"):
        async with maker() as session:
            await projection.commit_release_projection(
                session,
                task_uuid="release-unknown",
                country_codes=["ZZ"],
                full=False,
                dirty_ids=[marker_id],
            )
    publish.assert_not_awaited()
    async with maker() as session:
        assert (await session.get(PublishDirty, marker_id)).published_at is None


@pytest.mark.parametrize("disabled_code", ["IE", "JP"])
@pytest.mark.parametrize("include_us", [False, True])
async def test_disabled_scope_stays_pending_without_blocking_publishable_scopes(
    release_db, monkeypatch, disabled_code, include_us
):
    maker, service, _ = release_db
    if disabled_code == "IE":
        country_config = module.get_country_bootstrap_config
        monkeypatch.setattr(module, "get_country_bootstrap_config", lambda code: (
            {**country_config(code), "public_release_enabled": False}
            if code == "IE" else country_config(code)
        ))
    async with maker() as session:
        if disabled_code == "IE":
            session.add(
                Country(code="IE", name="Ireland", name_en="Ireland", is_active=True)
            )
            assert (
                module.get_country_bootstrap_config("IE")["public_release_enabled"]
                is False
            )
        else:
            country = (
                await session.execute(select(Country).where(Country.code == "JP"))
            ).scalar_one()
            country.is_active = False
        disabled = PublishDirty(
            line="surveillance",
            scope_key=disabled_code,
            source_task_uuid="disabled",
            marked_at=datetime.now(timezone.utc),
        )
        session.add(disabled)
        us = None
        if include_us:
            us = PublishDirty(
                line="surveillance",
                scope_key="US",
                source_task_uuid="us",
                marked_at=datetime.now(timezone.utc),
            )
            session.add(us)
        await session.commit()
        disabled_id, us_id = disabled.id, us.id if us else None
    task = await enqueue(maker, service)
    assert task.input_data["incremental_country_codes"] == (
        ["US"] if include_us else []
    )
    assert task.input_data["publish_dirty_ids"] == ([us_id] if include_us else [])
    assert task.input_data["incremental_site"] is True
    context = AsyncMock(return_value={"countries_by_code": {"US": {}}})
    publish = AsyncMock()
    monkeypatch.setattr(projection, "_load_publish_context", context)
    monkeypatch.setattr(projection, "_publish_country", publish)
    async with maker() as session:
        await projection.commit_release_projection(
            session,
            task_uuid=task.task_uuid,
            country_codes=task.input_data["incremental_country_codes"],
            full=False,
            dirty_ids=task.input_data["publish_dirty_ids"],
        )
        await session.commit()
    assert [call.args[1] for call in publish.await_args_list] == (
        ["US"] if include_us else []
    )
    if not include_us:
        context.assert_not_awaited()
    async with maker() as session:
        assert (await session.get(PublishDirty, disabled_id)).published_at is None
        if us_id:
            assert (
                await session.get(PublishDirty, us_id)
            ).published_task_uuid == task.task_uuid
    if disabled_code == "IE":
        assert (
            module.get_country_bootstrap_config("IE")["public_release_enabled"] is False
        )


async def test_full_release_does_not_acknowledge_disabled_country(
    release_db, monkeypatch
):
    maker, service, _ = release_db
    async with maker() as session:
        session.add(Country(code="IE", name="Ireland", name_en="Ireland"))
        marker = PublishDirty(
            line="surveillance",
            scope_key="IE",
            source_task_uuid="ie",
            marked_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
        )
        session.add(marker)
        await session.commit()
        marker_id = marker.id
    task = await enqueue(maker, service, full=True)
    monkeypatch.setattr(
        projection,
        "_load_publish_context",
        AsyncMock(return_value={"countries_by_code": {"US": {}, "JP": {}}}),
    )
    monkeypatch.setattr(projection, "_publish_country", AsyncMock())
    async with maker() as session:
        await projection.commit_release_projection(
            session,
            task_uuid=task.task_uuid,
            country_codes=[],
            full=True,
            dirty_ids=task.input_data["publish_dirty_ids"],
            marked_before=task.input_data["publish_dirty_before"],
        )
        await session.commit()
    async with maker() as session:
        assert (await session.get(PublishDirty, marker_id)).published_at is None


async def test_frozen_release_does_not_consume_same_country_change_committed_during_build(release_db, monkeypatch, tmp_path):
    from src.core.database_snapshot import SNAPSHOT_ENV
    from src.generation.release_projection_snapshot import write_projection_snapshot
    from src.generation import public_series_store

    maker, _, _ = release_db
    async with maker() as db:
        old = PublishDirty(line='surveillance', scope_key='US', source_task_uuid='old', marked_at=datetime.now(timezone.utc))
        db.add(old)
        await db.commit()
        old_id = old.id
    monkeypatch.setenv(SNAPSHOT_ENV, '00000003-0000001B-1')
    write_projection_snapshot({
        'incremental': True, 'incremental_country_codes': ['US'],
        'country_exports': [{'code': 'US', 'country_data': {'disease_series': {'D001': {
            'dates': ['2026-01-01'], 'cases': [7], 'deaths': [0], 'incidence_rates': [0.2],
            'period_granularity': 'year',
        }}}}],
    }, tmp_path, {})
    # A late commit may carry an old marked_at. A timestamp cutoff must never
    # absorb it, even when it targets the same country as the current release.
    async with maker() as db:
        newer = PublishDirty(line='surveillance', scope_key='US', source_task_uuid='new', marked_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.add(newer)
        await db.commit()
        new_id = newer.id
    monkeypatch.setattr(projection, '_load_publish_context', AsyncMock(return_value={
        'countries_by_code': {'US': {}}, 'stored_disease_ids': {'D001'},
    }))
    monkeypatch.setattr(projection, '_retire_disabled_publications', AsyncMock(return_value=set()))
    monkeypatch.setattr(projection, '_publish_country', AsyncMock(side_effect=AssertionError('must not reread newer facts')))
    replace = AsyncMock()
    monkeypatch.setattr(public_series_store, '_replace_country_rows', replace)
    async with maker() as db:
        await projection.commit_release_projection(
            db, task_uuid='release', country_codes=['US'], full=False,
            dirty_ids=[old_id], prepared_dir=tmp_path, require_prepared=True,
        )
        await db.commit()
    assert replace.await_args.args[2][0]['cases'] == [7]
    async with maker() as db:
        assert (await db.get(PublishDirty, old_id)).published_task_uuid == 'release'
        assert (await db.get(PublishDirty, new_id)).published_at is None
