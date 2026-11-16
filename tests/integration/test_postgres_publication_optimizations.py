"""Real asyncpg limits and publication transactions in a disposable schema.

TEST_SNAPSHOT_DATABASE_URL must point to an isolated test PostgreSQL database.
All writes remain inside a unique schema that is dropped after each test.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Country, DataReleaseJob, Task
from src.domain.report import Report
from src.domain.collection import PublishDirty
from src.services import data_release_service as release
from src.services.data_release import data_selection, projection
from src.services.data_release.global_selection import (
    acknowledge_content_selection, acknowledge_global_selection, selection_batches,
    selected_id_predicate,
)
from src.services.data_release.publication_execution import PublicationPlanError


@pytest.fixture(params=["force_generic_plan", "force_custom_plan"])
async def publication_database(monkeypatch, request):
    url = os.environ.get("TEST_SNAPSHOT_DATABASE_URL")
    if not url:
        pytest.skip("TEST_SNAPSHOT_DATABASE_URL must point to a disposable PostgreSQL database")
    schema = "test_publication_optimizations_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {
        "search_path": schema, "plan_cache_mode": request.param, "statement_timeout": "10000",
    }})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                Country.__table__, Report.__table__, Task.__table__, DataReleaseJob.__table__, PublishDirty.__table__,
            ]))
        async with maker() as db:
            db.add_all([Country(code=code, name=code, name_en=code) for code in ("US", "US-AK", "JP")])
            db.add(DataReleaseJob(job_id="site-release", name="Site"))
            await db.commit()

        @asynccontextmanager
        async def database():
            async with maker() as db:
                yield db

        monkeypatch.setattr(release, "get_database", database)
        monkeypatch.setattr(release.task_manager, "_broadcast", AsyncMock())
        monkeypatch.setattr(release, "get_country_bootstrap_config", lambda code: {})
        monkeypatch.setattr(data_selection, "get_country_bootstrap_config", lambda code: {})
        # These tests exercise real marker transactions; projection contents
        # have separate tests and never read facts outside this isolated schema.
        monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
            "countries_by_code": {"US": {}, "US-AK": {}, "JP": {}},
        }))
        monkeypatch.setattr(projection, "_publish_country", AsyncMock())
        monkeypatch.setattr(projection, "_retire_disabled_publications", AsyncMock(return_value=set()))
        service = release.DataReleaseService()
        monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(timezone="UTC", batch_max_countries=1))
        monkeypatch.setattr(service, "_download_repo_branch", lambda job: "main")
        monkeypatch.setattr(service, "_cloudflare_project_name", lambda name: "globalid")
        monkeypatch.setattr(service, "_download_repo_url", lambda: "https://example.invalid/downloads.git")
        monkeypatch.setattr(service, "_download_repo_raw_base", lambda job: "https://example.invalid/raw")
        monkeypatch.setattr(service, "_raw_archive_runtime", lambda: SimpleNamespace(enabled=False, branch="main", repo_url=""))
        monkeypatch.setattr(service, "_automatic_retry_settings", lambda: (0, 0, 0))
        yield maker, service, engine
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


async def seed(maker, count, *, line="surveillance", first_id=1):
    now = datetime.now(timezone.utc)
    async with maker() as db:
        for batch in selection_batches(list(range(first_id, first_id + count))):
            await db.execute(insert(PublishDirty), [{
                "id": marker_id, "line": line, "scope_key": "US" if line == "surveillance" else "*",
                "source_task_uuid": "collection", "marked_at": now,
                # A legacy marker in the last batch must expand its whole
                # parent group, even when the country budget is only one.
                "change_set": None if marker_id == first_id + count - 1 else {"version": 1, "precise": True},
            } for marker_id in batch])
        await db.commit()
    return list(range(first_id, first_id + count))


@pytest.mark.parametrize("count", [1, 4999, 5000, 5001, 32768, 100000])
async def test_reviewed_selection_and_projection_above_asyncpg_parameter_limit(publication_database, count):
    maker, service, engine = publication_database
    ids = await seed(maker, count)
    bind_counts = []

    def record(_connection, _cursor, statement, parameters, _context, _executemany):
        if "publish_dirty" in statement and statement.lstrip().startswith(("SELECT", "UPDATE")):
            bind_counts.append(len(parameters))
            if 'ANY (' in statement:
                predicate = statement.split('WHERE', 1)[1].split('RETURNING', 1)[0]
                assert 'publish_dirty.line' not in predicate
                assert 'publish_dirty.published_at' not in predicate

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    task_uuid = str(uuid4())
    result = await service._enqueue_release_task(
        release.DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True,
        pending_ids=list(reversed(ids)), publication_plan={"task_uuid": task_uuid, "plan_digest": "test"},
    )
    assert result["status"] == "queued"
    async with maker() as db:
        task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == ids
        assert task.input_data["incremental_country_codes"] == ["US", "US-AK"]
        assert task.input_data["continuation_dirty_ids"] == []
    later = (await seed(maker, 1, first_id=count + 1))[0]
    async with maker() as db:
        plan = await data_selection.validate_data_selection(db, country_codes=["US", "US-AK"],
            dirty_ids=list(reversed(ids)), expected_task_uuid=task_uuid)
        assert plan["dirty_ids"] == ids
        await projection.commit_release_projection(db, task_uuid=task_uuid,
            country_codes=plan["country_codes"], country_scopes_exact=True, full=False, dirty_ids=ids)
        await db.commit()
    async with maker() as db:
        acknowledged = (await db.execute(select(func.count(), func.count(func.distinct(PublishDirty.published_at)))
            .select_from(PublishDirty).where(PublishDirty.published_task_uuid == task_uuid))).one()
        assert acknowledged == (count, 1)
        assert (await db.get(PublishDirty, later)).published_at is None
        timestamp = (await db.get(PublishDirty, ids[0])).published_at
        # Its own acknowledged selection remains retryable and keeps its first
        # acknowledgement time, while a newer marker remains pending.
        await data_selection.validate_data_selection(db, country_codes=["US", "US-AK"],
            dirty_ids=ids, expected_task_uuid=task_uuid)
        await projection.commit_release_projection(db, task_uuid=task_uuid,
            country_codes=["US", "US-AK"], country_scopes_exact=True, full=False, dirty_ids=ids)
        await db.commit()
        assert (await db.get(PublishDirty, ids[0])).published_at == timestamp
        assert (await db.get(PublishDirty, later)).published_at is None
    assert max(bind_counts) <= 5010


@pytest.mark.parametrize("kind", ["projection", "content"])
async def test_late_batch_failure_rolls_back_every_acknowledgement(publication_database, monkeypatch, kind):
    maker, _, _ = publication_database
    ids = await seed(maker, 5001, line="knowledge")
    calls = 0
    with pytest.raises(RuntimeError, match="second batch"):
        async with maker() as db:
            execute = db.execute

            async def fail_late(statement, *args, **kwargs):
                nonlocal calls
                if statement.is_update and statement.table.name == "publish_dirty":
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("second batch failed")
                return await execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", fail_late)
            if kind == "projection":
                await projection.commit_release_projection(db, task_uuid="publisher",
                    country_codes=[], full=False, dirty_ids=ids)
            else:
                await acknowledge_content_selection(db, ids, task_uuid="publisher",
                    situation_detached=True, literature_detached=True)
            await db.commit()
    assert calls == 2
    async with maker() as db:
        assert (await db.execute(select(func.count()).select_from(PublishDirty).where(
            PublishDirty.published_at.is_(None)))).scalar_one() == len(ids)


@pytest.mark.parametrize("count", [32768, 100000])
async def test_full_reviewed_content_selection_preserves_detached_and_new_markers(publication_database, count):
    maker, service, _ = publication_database
    ids = await seed(maker, count, line="knowledge")
    task_uuid = str(uuid4())
    result = await service._enqueue_release_task(
        release.DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, full=True,
        pending_ids=list(reversed(ids)), publication_plan={"task_uuid": task_uuid, "plan_digest": "test"})
    assert result["status"] == "queued"
    excluded = []
    for offset, line in enumerate(("surveillance", "situation", "literature"), count + 1):
        excluded.extend(await seed(maker, 1, line=line, first_id=offset))
    later = (await seed(maker, 1, line="knowledge", first_id=count + 4))[0]
    async with maker() as db:
        task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == ids
        # Even incorrectly supplied detached IDs cannot be consumed by static
        # content; this protects old/manual task inputs as well.
        await acknowledge_content_selection(db, ids + excluded, task_uuid=task_uuid,
            situation_detached=True, literature_detached=True)
        await db.commit()
    async with maker() as db:
        assert set((await db.execute(select(PublishDirty.id).where(PublishDirty.published_at.is_(None)))).scalars()) == set(excluded + [later])
        assert (await db.execute(select(func.count(func.distinct(PublishDirty.published_at))).select_from(PublishDirty))).scalar_one() == 1


@pytest.mark.parametrize("line", ["surveillance", "situation", "literature"])
async def test_last_batch_owned_by_another_publisher_is_rejected(publication_database, line):
    maker, _, _ = publication_database
    ids = await seed(maker, 5001, line=line)
    async with maker() as rival:
        await rival.execute(update(PublishDirty).where(PublishDirty.id == ids[-1]).values(
            published_at=datetime.now(timezone.utc), published_task_uuid="rival"))
        await rival.commit()
    async with maker() as db:
        with pytest.raises(ValueError, match="another task|already consumed"):
            if line == "surveillance":
                await data_selection.validate_data_selection(db, country_codes=["US", "US-AK"],
                    dirty_ids=ids, expected_task_uuid="publisher")
            else:
                await acknowledge_global_selection(db, ids, line=line, task_uuid="publisher")
    async with maker() as db:
        assert (await db.execute(select(func.count()).select_from(PublishDirty).where(
            PublishDirty.published_task_uuid == "publisher"))).scalar_one() == 0


async def test_reviewed_plan_rechecks_late_batch_after_reserving_queue(publication_database, monkeypatch):
    maker, service, _ = publication_database
    ids = await seed(maker, 5001)
    opened = 0

    @asynccontextmanager
    async def database():
        nonlocal opened
        opened += 1
        if opened == 2:
            async with maker() as rival:
                await rival.execute(update(PublishDirty).where(PublishDirty.id == ids[-1]).values(
                    published_at=datetime.now(timezone.utc), published_task_uuid="rival"))
                await rival.commit()
        async with maker() as db:
            yield db

    monkeypatch.setattr(release, "get_database", database)
    with pytest.raises(PublicationPlanError, match="publication_selection_changed"):
        await service._enqueue_release_task(release.DataReleaseJobConfig(job_id="site-release", name="Site"),
            manual=True, trigger="test", trigger_task_uuid=None, data_only=True, pending_ids=ids,
            publication_plan={"task_uuid": str(uuid4()), "plan_digest": "test"})
    async with maker() as db:
        assert (await db.execute(select(func.count()).select_from(Task))).scalar_one() == 0


async def test_empty_selection_never_becomes_full_or_acknowledges_pending(publication_database):
    maker, service, _ = publication_database
    ids = await seed(maker, 1)
    result = await service._enqueue_release_task(release.DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="test", trigger_task_uuid=None, data_only=True, pending_ids=[])
    assert result["status"] == "skipped"
    async with maker() as db:
        with pytest.raises(ValueError, match="unique positive"):
            await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[])
        await projection.commit_release_projection(db, task_uuid="publisher", country_codes=[], full=False, dirty_ids=[])
        await db.commit()
        assert (await db.get(PublishDirty, ids[0])).published_at is None


@pytest.mark.parametrize('kind', ['projection', 'content'])
@pytest.mark.parametrize('invalid', ['missing', 'rival'])
async def test_acknowledgement_rechecks_last_batch_and_rolls_back_earlier_batches(publication_database, kind, invalid):
    maker, _, _ = publication_database
    ids = await seed(maker, 5001, line='knowledge')
    async with maker() as rival:
        if invalid == 'missing':
            from sqlalchemy import delete
            await rival.execute(delete(PublishDirty).where(PublishDirty.id == ids[-1]))
        else:
            await rival.execute(update(PublishDirty).where(PublishDirty.id == ids[-1]).values(
                published_at=datetime.now(timezone.utc), published_task_uuid='rival'))
        await rival.commit()
    async with maker() as db:
        with pytest.raises(ValueError, match='missing|already consumed'):
            if kind == 'projection':
                await projection.commit_release_projection(db, task_uuid='publisher', country_codes=[],
                                                          full=False, dirty_ids=ids)
            else:
                await acknowledge_content_selection(db, ids, task_uuid='publisher',
                                                    situation_detached=True, literature_detached=True)
        await db.rollback()
    async with maker() as db:
        assert (await db.execute(select(func.count()).select_from(PublishDirty).where(
            PublishDirty.published_task_uuid == 'publisher'))).scalar_one() == 0


async def test_locked_id_read_refreshes_identity_map_and_holds_lock_until_transaction_end(publication_database):
    maker, _, _ = publication_database
    ids = await seed(maker, 1, line='literature')
    async with maker() as db:
        stale = await db.get(PublishDirty, ids[0])
        async with maker() as rival:
            await rival.execute(update(PublishDirty).where(PublishDirty.id == ids[0]).values(
                published_at=datetime.now(timezone.utc), published_task_uuid='rival'))
            await rival.commit()
        assert stale.published_at is None
        with pytest.raises(ValueError, match='already consumed'):
            await acknowledge_global_selection(db, ids, line='literature', task_uuid='publisher')
        assert stale.published_task_uuid == 'rival'
        async with maker() as contender:
            await contender.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError, match='lock timeout'):
                await contender.execute(update(PublishDirty).where(PublishDirty.id == ids[0]).values(
                    published_task_uuid='contender'))
            await contender.rollback()
        await db.rollback()


@pytest.mark.parametrize('count', [32768, 100000])
async def test_global_acknowledgement_above_bind_limit_keeps_own_timestamp_and_later_marker(publication_database, count):
    maker, _, _ = publication_database
    ids = await seed(maker, count, line='literature')
    later = (await seed(maker, 1, line='literature', first_id=count + 1))[0]
    async with maker() as db:
        await acknowledge_global_selection(db, ids, line='literature', task_uuid='global-publisher')
        await db.commit()
        original = (await db.get(PublishDirty, ids[0])).published_at
        await acknowledge_global_selection(db, ids, line='literature', task_uuid='global-publisher')
        await db.commit()
        assert (await db.get(PublishDirty, ids[0])).published_at == original
        assert (await db.get(PublishDirty, later)).published_at is None
        assert (await db.execute(select(func.count(), func.count(func.distinct(PublishDirty.published_at)))
            .select_from(PublishDirty).where(PublishDirty.published_task_uuid == 'global-publisher'))).one() == (count, 1)


async def test_large_id_only_select_and_update_use_pk_without_statistics(publication_database):
    maker, _, _ = publication_database
    ids = await seed(maker, 100000)
    async with maker() as db:
        # PREPARE explicitly exercises the server's cached-plan choice with
        # the same typed single array emitted by the production helper.
        stats = (await db.execute(text("SELECT reltuples FROM pg_class WHERE oid='publish_dirty'::regclass"))).scalar_one()
        assert stats == -1  # Do not hide the regression with ANALYZE.
        await db.execute(text('PREPARE marker_select(bigint[]) AS SELECT id,line,published_at FROM publish_dirty WHERE id=ANY($1) ORDER BY id FOR UPDATE'))
        await db.execute(text("PREPARE marker_ack(bigint[]) AS UPDATE publish_dirty SET published_task_uuid='probe',published_at=now() WHERE id=ANY($1)"))
        arguments = ','.join(str(value) for value in ids[:5000])
        import json
        for command in ['marker_select', 'marker_ack']:
            raw = (await db.execute(text(f'EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) EXECUTE {command}(ARRAY[{arguments}]::bigint[])'))).scalar_one()
            plan = json.loads(raw) if isinstance(raw, str) else raw
            def walk(node):
                yield node
                for child in node.get('Plans', []):
                    yield from walk(child)
            mode = (await db.execute(text('SHOW plan_cache_mode'))).scalar_one()
            if mode == 'force_generic_plan':
                assert any(node.get('Index Name') == 'publish_dirty_pkey' for node in walk(plan[0]['Plan']))
            assert not any(node.get('Index Name') == 'ix_publish_dirty_pending' for node in walk(plan[0]['Plan']))
            assert plan[0]['Execution Time'] < 5000
        await db.rollback()
        predicate = selected_id_predicate(db, ids[:5000])
        from sqlalchemy.dialects.postgresql.asyncpg import dialect
        compiled = select(PublishDirty.id).where(predicate).compile(dialect=dialect())
        assert len(compiled.params) == 1 and 'ANY' in str(compiled)
