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

from src.domain import Base, Country, DataReleaseJob, Task, TaskStatus
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


async def test_full_release_does_not_read_dirty_scopes(release_db):
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
    assert not any("FROM publish_dirty" in statement for statement in statements)
    assert task.input_data["full"] is True
    assert task.input_data["incremental_site"] is False
    assert task.input_data["publish_dirty_ids"] == []
    assert task.input_data["publish_dirty_before"]
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
            dirty_ids=[],
            marked_before=task.input_data["publish_dirty_before"],
        )
        await session.commit()
    async with maker() as session:
        assert (await session.get(PublishDirty, marker_id)).published_at is None
