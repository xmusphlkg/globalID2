"""Real database concurrency tests. Use a disposable TEST_AI_DATABASE_URL."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai.runtime import store
from src.ai.runtime.signals import CHANNEL, ActivitySignals, publish_changes
from src.domain import Base, Task
from src.domain.ai_work_item import AIWorkItem


@pytest.fixture
async def database(monkeypatch):
    url = os.environ.get("TEST_AI_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_AI_DATABASE_URL must point to a disposable PostgreSQL database"
        )
    schema = "ai_test_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        pool_size=12,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    tables = {Task.__table__, AIWorkItem.__table__}
    while True:
        expanded = tables | {
            fk.column.table for table in tables for fk in table.foreign_keys
        }
        if expanded == tables:
            break
        tables = expanded
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=list(tables))
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with sessions.begin() as session:
            yield session

    from src.domain import TaskStatus, TaskType

    async with factory() as session:
        session.add(
            Task(
                task_uuid="parent",
                task_name="Runtime integration",
                task_type=TaskType.GENERATE_REPORT,
                status=TaskStatus.RUNNING,
            )
        )
    monkeypatch.setattr(store, "get_database", factory)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


def route(key="provider:model", capacity=2):
    return {
        "model_key": key,
        "runtime_model_capacity": capacity,
        "available_for_routing": True,
    }


async def enqueue(count=1):
    async with store.get_database() as session:
        items = await store.submit_activities(
            session,
            parent_task_uuid="parent",
            line="report_section",
            priority="high",
            payloads=[{"request_key": str(uuid4())} for _ in range(count)],
        )
        return [item.id for item in items]


async def test_global_route_capacity_and_unique_claims(database):
    await enqueue(12)
    results = await asyncio.gather(
        *(
            store.claim_work_batch(route(), limit=1, owner=f"executor-{i % 3}")
            for i in range(12)
        )
    )
    leased = [item for batch in results for item in batch]
    assert len(leased) == 2
    assert len({item.id for item in leased}) == 2
    assert len({item.lease_token for item in leased}) == 2
    assert await store.active_route_lease_counts() == {"provider:model": 2}


async def test_locked_head_does_not_block_next_activity(database):
    first, second = await enqueue(2)
    async with database() as lock:
        await lock.execute(
            select(AIWorkItem).where(AIWorkItem.id == first).with_for_update()
        )
        async with asyncio.timeout(3):
            item = (
                await store.claim_work_batch(route(), limit=1, owner="other-process")
            )[0]
        assert item.id == second


async def test_renewal_owner_release_and_fencing(database):
    await enqueue(2)
    first = (await store.claim_work_batch(route(), limit=1, owner="one"))[0]
    second = (await store.claim_work_batch(route(), limit=1, owner="two"))[0]
    assert await store.renew_owned_leases("one") == {first.id}
    async with database() as session:
        assert store._owns_lease(await session.get(AIWorkItem, first.id), first)
    assert await store.release_owned_leases("one") == 1
    replacement = (await store.claim_work_batch(route(), limit=1, owner="three"))[0]
    assert replacement.id == first.id and replacement.lease_token != first.lease_token
    async with database() as session:
        assert not store._owns_lease(await session.get(AIWorkItem, first.id), first)
        assert (await session.get(AIWorkItem, second.id)).lease_owner == "two"
    assert (
        await store.recover_expired_leases(now=store._now() + timedelta(seconds=61))
        == 2
    )


async def test_activity_insert_and_notification_are_transactional(database):
    import asyncpg

    connection = await asyncpg.connect(
        os.environ["TEST_AI_DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
    )
    messages = []
    await connection.add_listener(CHANNEL, lambda *_args: messages.append(_args[-1]))
    try:
        with pytest.raises(ValueError):
            async with database() as session:
                await store.submit_activity(
                    session,
                    parent_task_uuid="rollback",
                    line="report_section",
                    priority="high",
                    payload={"request_key": "rollback"},
                )
                raise ValueError("rollback")
        await asyncio.sleep(0.05)
        assert not messages
        async with database() as session:
            assert await session.scalar(select(AIWorkItem.id)) is None
        await enqueue()
        async with asyncio.timeout(2):
            while not messages:
                await asyncio.sleep(0.01)
    finally:
        await connection.close()


async def test_targeted_notification_and_lost_hint_reconciliation(database):
    first, second = await enqueue(2)
    hub = ActivitySignals(database, interval=0.05)
    async with (
        hub.subscribe([first], "first-parent") as first_event,
        hub.subscribe([second], "second-parent") as second_event,
    ):
        await asyncio.sleep(0.1)  # install LISTEN and complete initial snapshot
        first_event.clear()
        second_event.clear()
        async with database() as session:
            await publish_changes(session, [first])
        await asyncio.wait_for(first_event.wait(), 2)
        assert not second_event.is_set()
        async with database() as session:
            item = await session.get(AIWorkItem, second)
            item.status = "succeeded"  # deliberately omit notification
        await asyncio.wait_for(second_event.wait(), 2)


async def test_strict_reviewer_does_not_use_author_when_alternative_busy(database):
    async with database() as session:
        work_id = (
            await store.submit_activity(
                session,
                parent_task_uuid="parent",
                line="content_governance",
                priority="normal",
                payload={"avoid_models": ["model"], "require_distinct_model": True},
            )
        ).id
    assert (
        await store.claim_work_batch(
            route(), limit=1, owner="one", alternatives={"provider:model"}
        )
        == []
    )
    item = (
        await store.claim_work_batch(route("other:reviewer"), limit=1, owner="two")
    )[0]
    assert item.id == work_id


@pytest.mark.parametrize("parent_status", ["QUEUED", "FAILED", "CANCELLED"])
async def test_new_schema_migrations_upgrade_and_reverse(database, parent_status):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migrations = []
    for filename in ("0029_ai_review_workflows.py", "0030_ai_activity_leases.py"):
        path = (
            Path(__file__).resolve().parents[2] / "migrations" / "versions" / filename
        )
        spec = importlib.util.spec_from_file_location(filename[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrations.append(module)
    async with database() as session:
        from src.domain import TaskStatus, TaskType

        parent = Task(
            task_uuid=str(uuid4()),
            task_name="Pre-cutover review",
            task_type=TaskType.AGENT_WORKFLOW,
            status=TaskStatus[parent_status],
            input_data={
                "operation": "governance_stream",
                "arguments": {
                    "kind": "articles",
                    "fetch_size": 64,
                    "workers": 100,
                    "max_claims": 5,
                },
            },
        )
        session.add(parent)
        pending = await store.submit_activity(
            session,
            parent_task_uuid=parent.task_uuid,
            line="content_governance",
            priority="normal",
            payload={"request_key": "pre-cutover"},
        )
        connection = await session.connection()

        def verify(sync):
            for column in (
                "lease_owner",
                "lease_token",
                "leased_at",
                "deadline_at",
                "cancel_requested",
            ):
                sync.execute(
                    text(f"ALTER TABLE ai_work_items DROP COLUMN {column} CASCADE")
                )
            with Operations.context(MigrationContext.configure(sync)):
                for migration in migrations:
                    migration.upgrade()
                columns = {
                    c["name"] for c in inspect(sync).get_columns("ai_work_items")
                }
                assert {
                    "lease_owner",
                    "lease_token",
                    "leased_at",
                    "deadline_at",
                    "cancel_requested",
                } <= columns
                assert {"ai_review_workflows", "ai_review_drains"} <= set(
                    inspect(sync).get_table_names()
                )
                from sqlalchemy import MetaData, Table

                from scripts.check_collection_schema import compare_table_metadata
                from src.domain.ai_review_workflow import (
                    AIReviewDrain,
                    AIReviewWorkflow,
                )

                for model in (AIReviewDrain, AIReviewWorkflow):
                    reflected = Table(
                        model.__tablename__, MetaData(), autoload_with=sync
                    )
                    assert not compare_table_metadata(
                        model.__table__, reflected, sync.dialect
                    )
                for migration in reversed(migrations):
                    migration.downgrade()
                assert "ai_review_workflows" not in inspect(sync).get_table_names()
                for migration in migrations:
                    migration.upgrade()

        await connection.run_sync(verify)
        await session.refresh(parent)
        await session.refresh(pending)
        assert parent.input_data["arguments"] == {
            "kind": "articles",
            "buffer_size": 512,
            "max_items": 5,
        }
        assert pending.status == "failed" and "cutover" in pending.error


async def test_cancelled_http_keeps_global_permit_until_cleanup_finishes(
    database, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.ai.runtime import dispatcher as module

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(module, "note_parent_work", AsyncMock())
    first, second = await enqueue(2)
    started, closing, close_allowed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(item, _route):
        if item.id == first:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closing.set()
                await close_allowed.wait()
        return "done"

    async def routes():
        return [route(capacity=1)]

    dispatcher = module.AIWorkDispatcher(routes, execute, owner="http-owner")
    await dispatcher.tick()
    await asyncio.wait_for(started.wait(), 2)
    async with database() as session:
        item = await session.get(AIWorkItem, first, with_for_update=True)
        await store.request_cancellation(session, item)
    cancelling = asyncio.create_task(dispatcher.tick())
    try:
        await asyncio.wait_for(closing.wait(), 3)
        assert (
            await store.claim_work_batch(
                route(capacity=1), limit=1, owner="other-executor"
            )
            == []
        )
        close_allowed.set()
        await asyncio.wait_for(cancelling, 3)
        await asyncio.gather(*dispatcher.active.values())
        async with database() as session:
            assert (await session.get(AIWorkItem, first)).status == "cancelled"
            assert (await session.get(AIWorkItem, second)).status == "succeeded"
    finally:
        close_allowed.set()
        cancelling.cancel()
        for task in dispatcher.active.values():
            task.cancel()
        await asyncio.gather(
            cancelling, *dispatcher.active.values(), return_exceptions=True
        )
        await dispatcher.transport.close()


async def test_execution_generation_fences_old_provider_attempt(database, monkeypatch):
    from unittest.mock import AsyncMock

    from src.ai.runtime import dispatcher as module

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(module, "note_parent_work", AsyncMock())
    (work_id,) = await enqueue()
    claimed = (await store.claim_work_batch(route(), limit=1, owner="old-generation"))[
        0
    ]
    async with database() as session:
        parent = await session.scalar(select(Task).where(Task.task_uuid == "parent"))
        parent.metadata_ = {"ai_execution_generation": 1}
    cancellations = await store.claim_owned_cancellations("old-generation")
    assert [item.id for item in cancellations] == [work_id]
    complete = AsyncMock(return_value="obsolete")
    dispatcher = module.AIWorkDispatcher(complete=complete, owner="old-generation")
    await dispatcher._execute(claimed, route())
    complete.assert_not_awaited()
    async with database() as session:
        assert (await session.get(AIWorkItem, work_id)).status == "cancelled"
    await dispatcher.transport.close()


async def test_concurrent_batch_claims_share_global_route_permits(database):
    await enqueue(24)
    batches = await asyncio.gather(
        *(
            store.claim_work_batch(route(capacity=11), owner=f"executor-{i}", limit=8)
            for i in range(4)
        )
    )
    items = [item for batch in batches for item in batch]
    assert len(items) == 11
    assert len({item.id for item in items}) == 11
    assert len({item.lease_token for item in items}) == 11
    assert await store.active_route_lease_counts() == {"provider:model": 11}


async def test_batch_claim_continues_within_page_after_skipping_locked_candidates(
    database,
):
    ids = await enqueue(10)
    async with database() as lock:
        await lock.execute(
            select(AIWorkItem).where(AIWorkItem.id.in_(ids[:3])).with_for_update()
        )
        items = await asyncio.wait_for(
            store.claim_work_batch(
                route(capacity=4), owner="another-executor", limit=4
            ),
            2,
        )
    assert [item.id for item in items] == ids[3:7]


async def test_different_routes_do_not_duplicate_batch_activities(database):
    ids = await enqueue(20)
    batches = await asyncio.gather(
        store.claim_work_batch(route("one:model", capacity=16), owner="one"),
        store.claim_work_batch(route("two:model", capacity=16), owner="two"),
    )
    claimed = [item.id for batch in batches for item in batch]
    assert sorted(claimed) == ids
    assert len(claimed) == len(set(claimed))


async def test_full_batch_amortizes_reservation_queries_and_writes(database):
    from sqlalchemy import event

    await enqueue(store.CLAIM_BATCH_SIZE)
    async with database() as session:
        engine = session.get_bind()
    queries = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        queries.append((statement, executemany))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        items = await store.claim_work_batch(route(capacity=16), owner="batch")
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(items) == 16
    assert sum("pg_advisory_xact_lock" in sql for sql, _ in queries) == 1
    assert sum(sql.lstrip().startswith("SELECT") for sql, _ in queries) <= 5
    writes = [(sql, many) for sql, many in queries if sql.lstrip().startswith("UPDATE")]
    assert len(writes) == 1 and writes[0][1]


async def test_batch_submission_uses_one_lookup_insert_and_notification(database):
    from sqlalchemy import event

    async with database() as session:
        engine = session.get_bind()
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        async with database() as session:
            items = await store.submit_activities(
                session,
                parent_task_uuid="parent",
                line="literature_enrich",
                priority="low",
                payloads=[{"request_key": f"batch-{i}"} for i in range(8)],
            )
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len({item.id for item in items}) == 8
    assert sum(sql.lstrip().startswith("INSERT") for sql in statements) == 1
    assert sum("pg_notify" in sql for sql in statements) == 1
    assert len(statements) == 3


async def test_parent_lock_serializes_overlapping_batch_submissions(database):
    async def submit(keys):
        async with database() as session:
            await store._lock_reservation(session, "parent:parent")
            return await store.submit_activities(
                session,
                parent_task_uuid="parent",
                line="literature_enrich",
                priority="low",
                payloads=[{"request_key": key} for key in keys],
            )

    first, second = await asyncio.gather(
        submit(["a", "shared", "shared"]), submit(["shared", "b"])
    )
    assert first[1] is first[2]
    assert first[1].id == second[0].id
    async with database() as session:
        rows = (await session.scalars(select(AIWorkItem))).all()
    assert len(rows) == 3


async def test_batch_submission_rolls_back_all_new_rows_on_postgres(database):
    with pytest.raises(ValueError, match="abort submission"):
        async with database() as session:
            await store.submit_activities(
                session,
                parent_task_uuid="parent",
                line="literature_enrich",
                priority="low",
                payloads=[{"request_key": f"rollback-{i}"} for i in range(8)],
            )
            parent = await session.scalar(
                select(Task).where(Task.task_uuid == "parent")
            )
            parent.input_data = {"batch_submitted": True}
            raise ValueError("abort submission")
    async with database() as session:
        assert await session.scalar(select(AIWorkItem.id)) is None
        assert not (await session.scalar(select(Task))).input_data
