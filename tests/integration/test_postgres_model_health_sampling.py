"""Health telemetry must not serialize successful provider completion bursts."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai import model_center
from src.ai.catalogue import health
from src.domain import AIModelCallLog, AIModelConfig, AIProviderConfig, Base


@pytest.fixture
async def database(monkeypatch):
    url = os.environ.get("TEST_AI_DATABASE_URL")
    if not url:
        pytest.skip("TEST_AI_DATABASE_URL must be a disposable PostgreSQL database")
    schema = "ai_health_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        pool_size=8,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        AIProviderConfig.__table__,
                        AIModelConfig.__table__,
                        AIModelCallLog.__table__,
                    ],
                )
            )
        monkeypatch.setattr(model_center, "get_db", factory)
        async with factory() as db:
            provider = AIProviderConfig(
                provider_key="test", provider_name="test", display_name="test"
            )
            db.add(provider)
            await db.flush()
            model = AIModelConfig(
                provider_id=provider.id,
                model_key="test:model",
                model_name="model",
                display_name="model",
                last_check_status="available",
                extra_params={},
            )
            db.add(model)
            await db.flush()
            route = {"model_id": model.id, "model_key": model.model_key}
        yield factory, engine, route
    finally:
        await engine.dispose()
        async with control.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


async def add_logs(factory, route, amount):
    async with factory() as db:
        db.add_all(
            [
                AIModelCallLog(
                    request_id=str(uuid4()),
                    model_id=route["model_id"],
                    model_key=route["model_key"],
                    model_name="model",
                    provider_key="test",
                    agent_name="test",
                    status="success",
                    latency_ms=100,
                )
                for _ in range(amount)
            ]
        )


async def test_concurrent_successes_write_one_sample_and_preserve_all_call_counts(
    database, monkeypatch
):
    factory, engine, route = database
    await add_logs(factory, route, 24)
    now = health._utcnow()
    monkeypatch.setattr(health, "_utcnow", lambda: now)
    updates = []
    counts = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_update(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE ai_models"):
            updates.append(statement)
        if "count(" in statement and "ai_model_call_logs" in statement:
            counts.append(statement)

    results = await asyncio.gather(
        *[
            model_center.record_route_runtime_success(route, duration_seconds=0.1)
            for _ in range(16)
        ]
    )
    assert sum(result["recorded"] for result in results) == 1
    assert len(updates) == 1
    assert len(counts) == 1
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert (
            health._runtime_health_state(model.extra_params)["runtime_success_count"]
            == 24
        )
    await add_logs(factory, route, 5)
    assert not (await model_center.record_route_runtime_success(route))["recorded"]
    monkeypatch.setattr(health, "_utcnow", lambda: now + timedelta(seconds=5))
    assert (await model_center.record_route_runtime_success(route))["recorded"]
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert (
            health._runtime_health_state(model.extra_params)["runtime_success_count"]
            == 29
        )


async def test_locked_model_never_blocks_success_and_next_success_recovers(database):
    factory, _engine, route = database
    now = health._utcnow()
    payload = {}
    for _ in range(3):
        payload = health._write_runtime_failure(
            payload,
            kind="timeout",
            error="timeout",
            occurred_at=now,
            duration_seconds=1,
            cooldown_seconds=60,
        )
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        model.extra_params = payload
    async with factory() as locked:
        await locked.scalar(
            select(AIModelConfig)
            .where(AIModelConfig.id == route["model_id"])
            .with_for_update()
        )
        async with asyncio.timeout(1):
            result = await model_center.record_route_runtime_success(route)
        assert result == {"recorded": False, "reason": "busy_or_removed"}
    assert (await model_center.record_route_runtime_success(route))["recorded"]
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert not health._runtime_health_state(model.extra_params)[
            "runtime_failure_active"
        ]


async def test_new_failure_between_snapshot_and_lock_survives_old_success(
    database, monkeypatch
):
    factory, _engine, route = database
    count_read, continue_success = asyncio.Event(), asyncio.Event()
    now = health._utcnow()
    clock = [now]
    monkeypatch.setattr(health, "_utcnow", lambda: clock[0])

    @asynccontextmanager
    async def delayed_factory():
        async with factory() as db:
            scalar = db.scalar

            async def delayed(statement, *args, **kwargs):
                result = await scalar(statement, *args, **kwargs)
                if "count(" in str(statement) and "ai_model_call_logs" in str(
                    statement
                ):
                    count_read.set()
                    await continue_success.wait()
                return result

            db.scalar = delayed
            yield db

    monkeypatch.setattr(model_center, "get_db", delayed_factory)
    success = asyncio.create_task(model_center.record_route_runtime_success(route))
    try:
        await asyncio.wait_for(count_read.wait(), 2)
        clock[0] = now + timedelta(seconds=1)
        await model_center.record_route_runtime_failure(
            route, TimeoutError("timed out")
        )
        continue_success.set()
        assert not (await success)["recorded"]
        async with factory() as db:
            model = await db.get(AIModelConfig, route["model_id"])
            assert (
                health._runtime_health_state(model.extra_params)[
                    "runtime_failure_streak"
                ]
                == 1
            )
    finally:
        continue_success.set()
        await asyncio.gather(success, return_exceptions=True)


async def test_runtime_success_does_not_override_invalid_model_status(database):
    factory, _engine, route = database
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        model.last_check_status = "unavailable"
    await model_center.record_route_runtime_success(route)
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert model.last_check_status == "unavailable"


async def seed_expired_rate_limit(factory, route):
    now = health._utcnow()
    limited_at = now - timedelta(minutes=2)
    params = health._mark_payload_rate_limited(
        {}, now - timedelta(seconds=1), limited_at
    )
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        model.extra_params = params
        model.last_check_status = "rate_limited"
        model.last_checked_at = limited_at
    return {
        **route,
        "extra_params": params,
        "last_check_status": "rate_limited",
        "rate_limit_active": False,
        "model_last_rate_limit_at": limited_at.isoformat(),
        "model_rate_limit_count": 1,
    }


async def test_concurrent_rate_limit_recovery_writes_once_and_keeps_history(database):
    factory, engine, route = database
    snapshot = await seed_expired_rate_limit(factory, route)
    writes, locks = [], []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def observe(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE ai_models"):
            writes.append(statement)
        if "ai_models" in statement and "FOR UPDATE" in statement:
            locks.append(statement)

    await asyncio.gather(
        *[model_center.clear_route_rate_limit(snapshot) for _ in range(16)]
    )
    assert len(writes) == 1
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        state = health._extract_routing_state(model.extra_params)
        assert model.last_check_status == "available"
        assert state["rate_limit_count"] == 1
        assert state["last_rate_limit_at"] == snapshot["model_last_rate_limit_at"]
        assert "cooldown_until" not in state
        recovered_at = state["last_recovered_at"]
    writes.clear()
    locks.clear()
    await asyncio.gather(
        *[model_center.clear_route_rate_limit(snapshot) for _ in range(16)]
    )
    assert writes == locks == []
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert model.extra_params["routing_state"]["last_recovered_at"] == recovered_at


@pytest.mark.parametrize("same_timestamp", [False, True])
async def test_rate_limit_recovery_rechecks_new_429_between_read_and_lock(
    database, monkeypatch, same_timestamp
):
    factory, _engine, route = database
    snapshot = await seed_expired_rate_limit(factory, route)
    read, proceed = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def delayed_factory():
        async with factory() as db:
            execute = db.execute

            async def delayed(statement, *args, **kwargs):
                result = await execute(statement, *args, **kwargs)
                if str(statement).startswith("SELECT ai_models.extra_params,"):
                    read.set()
                    await proceed.wait()
                return result

            db.execute = delayed
            yield db

    monkeypatch.setattr(model_center, "get_db", delayed_factory)
    clear = asyncio.create_task(model_center.clear_route_rate_limit(snapshot))
    try:
        await asyncio.wait_for(read.wait(), 2)
        clock = (
            health._parse_datetime(snapshot["model_last_rate_limit_at"])
            if same_timestamp
            else health._utcnow()
        )
        monkeypatch.setattr(health, "_utcnow", lambda: clock)
        await model_center.mark_route_rate_limited(
            route, "a newer 429", retry_after_seconds=600
        )
        proceed.set()
        await clear
        async with factory() as db:
            model = await db.get(AIModelConfig, route["model_id"])
            state = health._rate_limit_state(model.extra_params, clock)
            assert model.last_check_status == "rate_limited"
            assert model.last_check_message == "a newer 429"
            assert state["rate_limit_count"] == 2
            assert state["rate_limit_remaining_seconds"] == 600
    finally:
        proceed.set()
        await asyncio.gather(clear, return_exceptions=True)


async def test_locked_rate_limit_recovery_does_not_block_success(database):
    factory, _engine, route = database
    snapshot = await seed_expired_rate_limit(factory, route)
    async with factory() as locked:
        await locked.scalar(
            select(AIModelConfig)
            .where(AIModelConfig.id == route["model_id"])
            .with_for_update()
        )
        async with asyncio.timeout(1):
            await model_center.clear_route_rate_limit(snapshot)
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert model.last_check_status == "rate_limited"
    await model_center.clear_route_rate_limit(snapshot)
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert model.last_check_status == "available"


async def test_rate_limit_recovery_never_revives_a_new_model_access_failure(database):
    factory, _engine, route = database
    snapshot = await seed_expired_rate_limit(factory, route)
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        model.last_check_status = "unavailable"
    await model_center.clear_route_rate_limit(snapshot)
    async with factory() as db:
        model = await db.get(AIModelConfig, route["model_id"])
        assert model.last_check_status == "unavailable"


async def test_success_index_migration_is_concurrent_reversible_and_covers_count(
    database,
):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    factory, engine, _route = database
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0031_ai_success_log_index.py"
    )
    spec = importlib.util.spec_from_file_location("success_index_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    ddl = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_ddl(_conn, _cursor, statement, _params, _context, _many):
        if "INDEX" in statement and migration.INDEX_NAME in statement:
            ddl.append(statement)

    for action in (
        migration.downgrade,
        migration.upgrade,
        migration.downgrade,
        migration.upgrade,
    ):
        async with engine.connect() as conn:

            def run(sync):
                context = MigrationContext.configure(sync)
                with context.begin_transaction(), Operations.context(context):
                    action()

            await conn.run_sync(run)
    assert len(ddl) == 4
    assert all("CONCURRENTLY" in statement for statement in ddl)
    async with factory() as db:
        valid = await db.scalar(
            text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid='idx_ai_model_call_logs_success_model'::regclass"
            )
        )
        assert valid
        await db.execute(
            text("""
            INSERT INTO ai_model_call_logs
                (request_id,model_key,model_name,provider_key,agent_name,status,latency_ms,created_at,updated_at)
            SELECT n::text, CASE WHEN n%10=0 THEN 'test:model' ELSE 'test:other' END,
                'model','test','test','success',100,now(),now()
            FROM generate_series(1,30000) n
        """)
        )
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE ai_model_call_logs"))
        plan = (
            await conn.execute(
                text(
                    "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT count(*) FROM ai_model_call_logs WHERE model_key='test:model' AND status='success'"
                )
            )
        ).scalar()[0]

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    index_scans = [
        node
        for node in nodes(plan["Plan"])
        if node.get("Index Name") == migration.INDEX_NAME
    ]
    assert len(index_scans) == 1
    assert index_scans[0]["Node Type"] == "Index Only Scan"
    assert index_scans[0]["Heap Fetches"] == 0
    print(
        f"success-count index-only execution: {plan['Execution Time']} ms (30000 synthetic logs)"
    )
