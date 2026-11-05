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
