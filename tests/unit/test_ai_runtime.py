"""Model work priority, route capacity, leases, restart replay and waiting slots."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")
from src.ai.runtime import (
    client,
    context,
    contracts,
    payloads,
    progress,
    signals,
    store,
)
from src.ai.runtime import dispatcher as dispatch

OWNER = "test-executor"
from src.domain import Base, Task, TaskPriority, TaskStatus, TaskType
from src.domain.ai_work_item import AIWorkItem


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    async with engine.begin() as conn:
        # Match PostgreSQL's reader/writer concurrency while exercising real
        # SQLite write reservations rather than silently ignoring FOR UPDATE.
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[Task.__table__, AIWorkItem.__table__]
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    for module in (client, context, dispatch, progress, store):
        monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setattr(payloads, "ROOT", tmp_path)
    signals._hubs[asyncio.get_running_loop()] = signals.ActivitySignals(
        database, interval=0.02
    )
    yield maker
    context._WAITING.clear()
    await engine.dispose()


def route(key="first", capacity=1, available=True):
    return {
        "model_key": key,
        "model_name": key,
        "runtime_model_capacity": capacity,
        "available_for_routing": available,
    }


async def enqueue(**kwargs):
    async with store.get_database() as session:
        parent_id = kwargs["parent_task_uuid"]
        if (
            parent_id != contracts.RUNTIME_PARENT
            and await session.scalar(select(Task.id).where(Task.task_uuid == parent_id))
            is None
        ):
            session.add(
                Task(
                    task_uuid=parent_id,
                    task_type=TaskType.GENERATE_REPORT,
                    task_name="test parent",
                    priority=TaskPriority.NORMAL,
                    status=TaskStatus.RUNNING,
                    input_data={},
                )
            )
    return await store.enqueue_work(**kwargs)


async def add(line="knowledge_brief", priority="low", parent="parent"):
    return await enqueue(
        parent_task_uuid=parent, line=line, priority=priority, payload={"sample": True}
    )


async def row(db, work_id):
    async with db() as session:
        return await session.get(AIWorkItem, work_id)


async def test_high_priority_report_claimed_before_low_backlog(db):
    low = await add()
    high = await add("report_section", "high")
    item = await store.claim_work(route(), owner=OWNER)
    assert item.id == high
    assert (await row(db, low)).status == "queued"
    assert await store.claim_work(route(), owner=OWNER) is None  # route permit is held
    assert (await store.claim_work(route("second"), owner=OWNER)).id == low


async def test_cooling_route_leaves_item_queued_for_other_route(db):
    work_id = await add()
    assert await store.claim_work(route(available=False), owner=OWNER) is None
    assert (await row(db, work_id)).status == "queued"
    assert (await store.claim_work(route("second"), owner=OWNER)).id == work_id


async def test_retry_prefers_another_idle_route_after_transient_failure(db):
    work_id = await add()
    live = [route(), route("second")]

    async def routes():
        return live

    async def complete(item, chosen):
        if chosen["model_key"] == "first":
            raise TimeoutError()
        return "recovered"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    first = await store.claim_work(live[0], owner=OWNER)
    await dispatcher._execute(first, live[0])
    waiting = await row(db, work_id)
    assert (waiting.status, waiting.attempt, waiting.payload["last_failed_route"]) == (
        "queued",
        2,
        "first",
    )
    assert store._utc(waiting.available_at) > datetime.now(timezone.utc)
    await make_ready(db)
    assert (
        await store.claim_work(live[0], alternatives={"first", "second"}, owner=OWNER)
        is None
    )
    second = await store.claim_work(
        live[1], alternatives={"first", "second"}, owner=OWNER
    )
    assert second.id == work_id
    await dispatcher._execute(second, live[1])
    assert (await row(db, work_id)).status == "succeeded"


@pytest.mark.parametrize(
    "alternatives,avoid",
    [
        ({"first"}, []),
        ({"first", "second"}, ["second"]),
    ],
)
async def test_failed_route_can_retry_when_no_eligible_alternative(
    db, alternatives, avoid
):
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="knowledge_brief",
        priority="low",
        payload={"avoid_models": avoid, "last_failed_route": "first"},
    )
    assert (
        await store.claim_work(route(), alternatives=alternatives, owner=OWNER)
    ).id == work_id


@pytest.mark.parametrize(
    "payload",
    [
        {"last_failed_route": "first"},
        {"avoid_models": ["first"]},
    ],
)
async def test_incompatible_idle_route_does_not_starve_vision_work(
    db, monkeypatch, payload
):
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="knowledge_brief",
        priority="low",
        payload=payload,
        capability="vision",
    )
    vision = {**route(), "capabilities": ["vision"]}
    text_only = {**route("second"), "capabilities": ["text"]}

    async def routes():
        return [vision, text_only]

    async def complete(item, chosen):
        assert chosen["model_key"] == "first"
        return "recovered"

    monkeypatch.setattr(
        dispatch.random, "choices", lambda options, weights: [options[0]]
    )
    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    assert await dispatcher.tick() == 1
    await asyncio.gather(*dispatcher.active.values())
    assert (await row(db, work_id)).status == "succeeded"


async def test_no_routable_routes_never_fail_parent_or_work(db):
    work_id = await add()

    async def routes():
        return [route(available=False), route("second", available=False)]

    dispatcher = dispatch.AIWorkDispatcher(route_loader=routes, owner=OWNER)
    assert await dispatcher.tick() == 0
    assert (await row(db, work_id)).status == "queued"
    assert (await row(db, work_id)).attempt == 1


async def test_expired_lease_reuses_id_and_attempt_is_bounded(db):
    work_id = await add()
    now = datetime.now(timezone.utc)
    for attempt in (1, 2, 3):
        leased = await store.claim_work(route(), now=now, owner=OWNER)
        assert leased.id == work_id
        assert leased.attempt == attempt
        now += timedelta(seconds=contracts.LEASE_SECONDS + 1)
        assert await store.recover_expired_leases(now=now) == 1
    item = await row(db, work_id)
    assert item.status == "failed"
    assert item.attempt == 3


async def test_dispatcher_calls_only_with_dispatch_context_and_requeues_rate_limit(db):
    work_id = await add()
    live_routes = [route(), route("second")]
    calls = []

    async def routes():
        return live_routes

    async def complete(item, chosen):
        assert context.in_dispatcher()
        calls.append(chosen["model_key"])
        if len(calls) == 1:
            chosen["available_for_routing"] = False
            raise RuntimeError("HTTP 429 rate limited")
        return "done"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    item = await row(db, work_id)
    assert item.status == "queued" and item.attempt == 2
    await make_ready(db)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    assert (await row(db, work_id)).status == "succeeded"
    assert calls[0] != calls[1]


async def test_submission_waits_for_local_lock_before_opening_database(db, monkeypatch):
    task = Task(
        task_uuid="parent",
        task_type=TaskType.GENERATE_REPORT,
        task_name="report",
        priority=TaskPriority.HIGH,
        status=TaskStatus.RUNNING,
        input_data={},
    )
    wrote = asyncio.Event()
    opened = 0
    original = client.get_database

    @asynccontextmanager
    async def counted_database():
        nonlocal opened
        opened += 1
        async with original() as session:
            yield session

    def write_request(*_args):
        loop.call_soon_threadsafe(wrote.set)
        return "request.json", "digest"

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(client, "get_database", counted_database)
    monkeypatch.setattr(client, "write_request", write_request)
    async with context.task_context(task) as state:
        async with state.enqueue_lock:
            pending = asyncio.create_task(
                client.submit_completion(fake_agent(), "prompt", None, False, {})
            )
            await asyncio.sleep(0.02)
            assert not wrote.is_set() and opened == 0
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_waiting_parent_releases_slots_and_completed_work_replays_after_restart(
    db,
):
    task = Task(
        task_uuid="parent",
        task_type=TaskType.GENERATE_REPORT,
        task_name="report",
        priority=TaskPriority.HIGH,
        status=TaskStatus.RUNNING,
        input_data={},
    )
    async with db() as session:
        session.add(task)
        await session.commit()
    agent = SimpleNamespace(
        name="writer", temperature=0.1, max_tokens=100, conversation_history=[]
    )

    async def routes():
        return [route()]

    calls = []

    async def complete(item, chosen):
        calls.append(item.id)
        # Queue payload contains a disk reference, not source document bytes.
        assert "prompt" not in item.payload
        return {"text": "result", "conversation": {"model": chosen["model_key"]}}

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(task):
        waiting = asyncio.create_task(
            client.submit_completion(agent, "prompt", "system", False, {})
        )
        for _ in range(100):
            if "parent" in context.waiting_parent_uuids():
                break
            await asyncio.sleep(0.01)
        assert "parent" in context.waiting_parent_uuids()
        async with db() as session:
            stored = (await session.execute(select(Task))).scalar_one()
            assert stored.status == TaskStatus.RUNNING
            assert stored.input_data["ai_waiting"] is True
        await dispatcher.tick()
        assert await asyncio.wait_for(waiting, 2) == "result"
    assert not context.waiting_parent_uuids()
    # A recovered parent replays a committed response instead of calling twice.
    async with context.task_context(task):
        assert (
            await client.submit_completion(agent, "prompt", "system", False, {})
            == "result"
        )
    assert len(calls) == 1


async def test_strict_lines_fail_parent_but_situation_tolerates_one_item(db):
    for task_type, strict in (
        (TaskType.GENERATE_REPORT, True),
        (TaskType.ENRICH_LITERATURE, True),
        (TaskType.UPDATE_DISEASE_KNOWLEDGE, True),
        (TaskType.REFRESH_SITUATION_SOURCES, False),
    ):
        task = SimpleNamespace(
            task_uuid="parent", task_type=task_type, priority="normal", input_data={}
        )
        async with context.task_context(task) as state:
            state.failures.append("one article failed")
            if strict:
                with pytest.raises(contracts.AIWorkFailed):
                    context.assert_children_succeeded()
            else:
                context.assert_children_succeeded()


async def test_health_check_runs_in_dispatcher_even_when_business_routes_cool(
    db, monkeypatch
):
    from src.ai import model_center

    called = []

    async def check(model_id, *, structured):
        called.append((model_id, structured, context.in_dispatcher()))
        return {"success": True, "model_id": model_id}

    monkeypatch.setattr(model_center, "check_model_by_id", check)

    async def routes():
        return [route(available=False)]

    work_id = await enqueue(
        parent_task_uuid="probe-parent",
        line="model_probe",
        priority="high",
        capability="health",
        payload={
            "operation": "check_model",
            "arguments": {"model_id": 42, "structured": True},
        },
    )
    regular = await add()
    dispatcher = dispatch.AIWorkDispatcher(route_loader=routes, owner=OWNER)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    assert called == [(42, True, True)]
    assert (await row(db, work_id)).result["operation_result"]["success"]
    assert (await row(db, regular)).status == "queued"


async def test_urgent_precedes_high_and_not_yet_available_work_is_skipped(db):
    high = await add(priority="high")
    urgent = await add(priority="urgent")
    future = await add(priority="urgent")
    async with db() as session:
        item = await session.get(AIWorkItem, future)
        item.available_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await session.commit()
    assert (await store.claim_work(route(), owner=OWNER)).id == urgent
    assert (await store.claim_work(route("second"), owner=OWNER)).id == high
    assert (await row(db, future)).status == "queued"


@pytest.mark.parametrize("capacity", [1, 2])
async def test_concurrent_claims_respect_route_capacity(db, capacity):
    for _ in range(4):
        await add()
    claims = await asyncio.gather(
        *(store.claim_work(route(capacity=capacity), owner=OWNER) for _ in range(4))
    )
    ids = [item.id for item in claims if item is not None]
    assert len(ids) == len(set(ids)) == capacity


async def test_batch_claim_respects_capacity_and_bounds_each_transaction(db):
    async with db() as session:
        session.add_all(
            [
                AIWorkItem(
                    parent_task_uuid="parent",
                    line="report_section",
                    priority="high",
                    payload={},
                    status="queued",
                    capability="text",
                )
                for _ in range(40)
            ]
        )
        await session.commit()
    first = await store.claim_work_batch(route(capacity=20), owner="one", limit=1000)
    second = await store.claim_work_batch(route(capacity=20), owner="two", limit=1000)
    assert len(first) == store.CLAIM_BATCH_SIZE
    assert len(second) == 20 - store.CLAIM_BATCH_SIZE
    assert len({item.id for item in first + second}) == 20
    assert len({item.lease_token for item in first + second}) == 20
    assert {item.lease_owner for item in first} == {"one"}
    assert {item.lease_owner for item in second} == {"two"}
    assert await store.claim_work_batch(route(capacity=20), owner="three") == []


async def test_batch_claim_preserves_priority_and_strict_model_diversity(db):
    low = await add(priority="low")
    high = await add(priority="high")
    blocked = await enqueue(
        parent_task_uuid="parent",
        line="report_review",
        priority="urgent",
        payload={"avoid_models": ["first"], "require_distinct_model": True},
    )
    urgent = await add(priority="urgent")
    items = await store.claim_work_batch(route(capacity=3), owner=OWNER)
    assert [item.id for item in items] == [urgent, high, low]
    assert (await row(db, blocked)).status == "queued"
    other = await store.claim_work_batch(route("second"), owner="reviewer")
    assert [item.id for item in other] == [blocked]


async def test_two_routes_cannot_lease_the_same_item(db):
    work_id = await add()
    claims = await asyncio.gather(
        store.claim_work(route(), owner=OWNER),
        store.claim_work(route("second"), owner=OWNER),
    )
    assert [item.id for item in claims if item is not None] == [work_id]


async def test_health_permits_are_atomic_across_dispatchers(db):
    extra = contracts.HEALTH_PROBE_PERMITS + 2
    for _ in range(extra):
        await enqueue(
            parent_task_uuid="parent",
            line="model_probe",
            priority="high",
            capability="health",
            payload={},
        )
    claims = await asyncio.gather(
        *(store.claim_probe(owner=OWNER) for _ in range(extra))
    )
    ids = [item.id for item in claims if item is not None]
    assert len(ids) == len(set(ids)) == contracts.HEALTH_PROBE_PERMITS


async def test_console_probe_is_claimed_before_background_sweep(db):
    background = await store.enqueue_runtime_probe()
    console = await enqueue(
        parent_task_uuid="parent",
        line="model_probe",
        priority="high",
        capability="health",
        payload={},
    )
    assert (await store.claim_probe(owner=OWNER)).id == console
    assert (await store.claim_probe(owner=OWNER)).id == background


async def test_background_sweep_is_not_queued_twice(db):
    first = await store.enqueue_runtime_probe()
    assert first is not None
    assert await store.enqueue_runtime_probe() is None
    await store.claim_probe(owner=OWNER)  # leased still counts as pending
    assert await store.enqueue_runtime_probe() is None
    async with db() as session:
        item = await session.get(AIWorkItem, first)
        item.status = "succeeded"
        await session.commit()
    assert await store.enqueue_runtime_probe() is not None


async def test_non_routable_route_is_not_claimed(db):
    work_id = await add()

    async def routes():
        return [{**route(), "available_for_routing": False, "rate_limit_active": True}]

    dispatcher = dispatch.AIWorkDispatcher(route_loader=routes, owner=OWNER)
    assert await dispatcher.tick() == 0
    assert (await row(db, work_id)).status == "queued"


async def test_dispatcher_starts_several_health_probes_in_one_tick(db, monkeypatch):
    from src.ai import model_center

    release = asyncio.Event()
    started = []

    async def check(model_id, *, structured):
        started.append(model_id)
        await release.wait()
        return {"success": True}

    monkeypatch.setattr(model_center, "check_model_by_id", check)
    for model_id in range(3):
        await enqueue(
            parent_task_uuid="parent",
            line="model_probe",
            priority="high",
            capability="health",
            payload={
                "operation": "check_model",
                "arguments": {"model_id": model_id, "structured": False},
            },
        )

    async def routes():
        return []

    dispatcher = dispatch.AIWorkDispatcher(route_loader=routes, owner=OWNER)
    await dispatcher.tick()
    await asyncio.sleep(0)
    assert sorted(started) == [0, 1, 2]
    release.set()
    await asyncio.gather(*dispatcher.active.values())


async def test_failed_agent_workflow_call_updates_progress_and_keeps_the_error(db):
    from src.domain import TaskWorkbook

    async with db() as session:
        connection = await session.connection()
        await connection.run_sync(
            lambda sync: TaskWorkbook.__table__.create(sync, checkfirst=True)
        )
        session.add(
            Task(
                task_uuid="parent-log",
                task_type=TaskType.AGENT_WORKFLOW,
                task_name="Model operation: governance_stream",
                priority=TaskPriority.LOW,
                status=TaskStatus.RUNNING,
                input_data={"operation": "governance_stream"},
                progress=0,
            )
        )
        await session.commit()
    work_id = await add(parent="parent-log")

    async def routes():
        return [route()]

    async def complete(item, chosen):
        raise TimeoutError("provider timed out")

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    for _ in range(3):
        await make_ready(db)
        await dispatcher.tick()
        await asyncio.gather(*dispatcher.active.values())
    async with db() as session:
        task = (
            await session.execute(select(Task).where(Task.task_uuid == "parent-log"))
        ).scalar_one()
        logs = (
            (await session.execute(select(TaskWorkbook).order_by(TaskWorkbook.id)))
            .scalars()
            .all()
        )
    assert task.progress == 99
    assert [log.title for log in logs] == [
        "Model call retry",
        "Model call retry",
        "Model call failed",
    ]
    assert "provider timed out" in logs[-1].content
    assert (await row(db, work_id)).status == "failed"


async def test_transient_failures_stop_after_three_attempts(db):
    work_id = await add()

    async def routes():
        return [route()]

    attempts = []

    async def complete(item, chosen):
        attempts.append(item.attempt)
        raise TimeoutError("provider timed out")

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    for _ in range(4):
        await make_ready(db)
        await dispatcher.tick()
        await asyncio.gather(*dispatcher.active.values())
    item = await row(db, work_id)
    assert attempts == [1, 2, 3]
    assert (item.status, item.attempt) == ("failed", 3)
    assert item.lease_expires_at is None and item.finished_at is not None


async def test_health_refresh_failure_does_not_leave_failed_call_leased(db):
    work_id = await add()

    async def routes():
        raise OSError("route health unavailable")

    async def complete(item, chosen):
        raise TimeoutError("provider timed out")

    item = await store.claim_work(route(), owner=OWNER)
    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher._execute(item, route())
    item = await row(db, work_id)
    assert (item.status, item.attempt) == ("queued", 2)


async def test_concurrent_recovery_does_not_advance_same_lease_twice(db):
    work_id = await add()
    now = datetime.now(timezone.utc)
    await store.claim_work(route(), now=now, owner=OWNER)
    now += timedelta(seconds=contracts.LEASE_SECONDS + 1)
    recovered = await asyncio.gather(
        store.recover_expired_leases(now=now), store.recover_expired_leases(now=now)
    )
    assert sum(recovered) == 1
    item = await row(db, work_id)
    assert (item.status, item.attempt) == ("queued", 2)


@pytest.mark.parametrize("fails", [False, True])
async def test_late_callback_cannot_overwrite_new_lease_on_same_attempt(db, fails):
    work_id = await add()
    old = await store.claim_work(route(), owner=OWNER)
    async with db() as session:
        current = await session.get(AIWorkItem, work_id)
        # A reclaimed activity has a new token even on the same route and attempt.
        # The old callback must be fenced from the new owner.
        current.lease_token = "new-generation-token"
        await session.commit()

    async def routes():
        return [route()]

    calls = []

    async def complete(item, chosen):
        calls.append(item.id)
        if fails:
            raise RuntimeError("HTTP 429 from obsolete call")
        return "obsolete response"

    await dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)._execute(
        old, route()
    )
    current = await row(db, work_id)
    assert (current.status, current.attempt, current.route_key) == (
        "leased",
        1,
        "first",
    )
    assert current.result is None and current.error is None
    assert calls == []


def fake_parent():
    return Task(
        task_uuid="parent",
        task_type=TaskType.GENERATE_REPORT,
        task_name="report",
        priority=TaskPriority.HIGH,
        status=TaskStatus.RUNNING,
        input_data={},
    )


def fake_agent(name="writer", max_tokens=100):
    return SimpleNamespace(
        name=name, temperature=0.1, max_tokens=max_tokens, conversation_history=[]
    )


async def wait_for_items(db, count):
    async def poll():
        while True:
            async with db() as session:
                items = list((await session.execute(select(AIWorkItem))).scalars())
            if len(items) >= count and "parent" in context.waiting_parent_uuids():
                return items
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), 3)


async def test_identical_concurrent_submissions_have_distinct_invocations(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    calls = []

    async def routes():
        return [route(capacity=2)]

    async def complete(item, chosen):
        calls.append(item.id)
        return "shared"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(task) as state:
        waiting = [
            asyncio.create_task(
                client.submit_completion(fake_agent(), "same", None, False, {})
            )
            for _ in range(2)
        ]
        try:
            await wait_for_items(db, 2)
            for _ in range(100):
                if state.waiting == 2:
                    break
                await asyncio.sleep(0.01)
            assert state.waiting == 2
            await dispatcher.tick()
            assert await asyncio.wait_for(asyncio.gather(*waiting), 3) == [
                "shared",
                "shared",
            ]
        finally:
            for pending in waiting:
                pending.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)
    assert len(calls) == 2
    async with db() as session:
        items = list((await session.execute(select(AIWorkItem))).scalars())
        assert len(items) == 2
        assert sorted(item.payload["invocation"] for item in items) == [0, 1]
    assert not context.waiting_parent_uuids()


async def test_restarted_parent_attaches_to_pending_work(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    async with context.task_context(task):
        waiting = asyncio.create_task(
            client.submit_completion(fake_agent(), "pending", None, False, {})
        )
        original = (await wait_for_items(db, 1))[0]
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    assert not context.waiting_parent_uuids()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        assert item.id == original.id
        return "resumed"

    async with context.task_context(task):
        waiting = asyncio.create_task(
            client.submit_completion(fake_agent(), "pending", None, False, {})
        )
        try:
            items = await wait_for_items(db, 1)
            assert [item.id for item in items] == [original.id]
            await dispatch.AIWorkDispatcher(routes, complete, owner=OWNER).tick()
            assert await asyncio.wait_for(waiting, 3) == "resumed"
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_finishing_one_of_two_calls_keeps_parent_waiting(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    gates = {"first": asyncio.Event(), "second": asyncio.Event()}

    async def routes():
        return [route(capacity=2)]

    async def complete(item, chosen):
        prompt = payloads.read_request(item.payload["request_path"])["prompt"]
        await gates[prompt].wait()
        return prompt

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(task) as state:
        pending = [
            asyncio.create_task(
                client.submit_completion(fake_agent(), prompt, None, False, {})
            )
            for prompt in gates
        ]
        try:
            await wait_for_items(db, 2)
            assert await dispatcher.tick() == 2
            gates["first"].set()
            assert await asyncio.wait_for(pending[0], 3) == "first"
            assert state.waiting == 1 and "parent" in context.waiting_parent_uuids()
            async with db() as session:
                stored = (await session.execute(select(Task))).scalar_one()
                assert stored.input_data["ai_waiting"] is True
            gates["second"].set()
            assert await asyncio.wait_for(pending[1], 3) == "second"
            assert state.waiting == 0 and not context.waiting_parent_uuids()
        finally:
            for gate in gates.values():
                gate.set()
            for waiting in pending:
                waiting.cancel()
            await asyncio.gather(
                *pending, *dispatcher.active.values(), return_exceptions=True
            )


async def test_parent_cancellation_persists_child_terminal_status(db):
    from src.services.exceptions import TaskCancelledError

    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    async with context.task_context(task):
        pending = asyncio.create_task(
            client.submit_completion(fake_agent(), "cancel", None, False, {})
        )
        await wait_for_items(db, 1)
        async with db() as session:
            parent = await session.scalar(select(Task))
            parent.metadata_ = {"cancel_requested": True}
            await session.commit()
        with pytest.raises(TaskCancelledError):
            await asyncio.wait_for(pending, 3)
    async with db() as session:
        child = await session.scalar(select(AIWorkItem))
        assert child.status == "cancelled" and child.finished_at is not None
        parent = await session.scalar(select(Task))
        assert parent.input_data["ai_waiting"] is False
    assert not context.waiting_parent_uuids()


async def test_waiting_ai_parent_does_not_block_the_ordinary_worker_lane(
    db, monkeypatch
):
    """AI organizer occupancy does not consume the independent crawl execution lane."""
    next_type = TaskType.CRAWL_DATA
    from unittest.mock import AsyncMock

    from src.services import task_worker as worker

    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()
    ordinary_started = asyncio.Event()
    stop_holder = []
    claimed = []
    completed = []

    async def recover(stop, owner):
        stop_holder.append(stop)
        await stop.wait()

    async def heartbeat(task_uuid, owner, finished):
        await finished.wait()

    async def claim(owner, *, excluded_task_uuids, **kwargs):
        if not claimed:
            claimed.append("parent")
            return "parent", TaskType.GENERATE_REPORT
        if len(claimed) == 1:
            if "parent" not in context.waiting_parent_uuids():
                return None
            assert "parent" in excluded_task_uuids
            assert next_type not in (kwargs.get("blocked_task_types") or ())
            claimed.append("next")
            return "next", next_type
        return None

    async def execute(task_uuid):
        if task_uuid == "parent":
            async with context.task_context(parent):
                assert (
                    await client.submit_completion(
                        fake_agent(), "worker", None, False, {}
                    )
                    == "done"
                )
        else:
            assert "parent" in context.waiting_parent_uuids()
            async with db() as session:
                stored = (await session.execute(select(Task))).scalar_one()
                assert stored.status == TaskStatus.RUNNING
                assert stored.input_data["ai_waiting"] is True
            ordinary_started.set()
        completed.append(task_uuid)
        if len(completed) == 2:
            stop_holder[0].set()

    class Dispatcher:
        async def run(self, stop):
            await ordinary_started.wait()

            async def routes():
                return [route()]

            async def complete(item, chosen):
                return "done"

            dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
            await dispatcher.tick()
            await asyncio.gather(*dispatcher.active.values())
            await stop.wait()

    registry = SimpleNamespace(
        new_instance_id=lambda kind: "test-worker",
        acquire_lease=AsyncMock(return_value=True),
        start_threaded_guard=lambda *args, **kwargs: SimpleNamespace(stop=lambda: None),
        release_lease=AsyncMock(),
        remove_heartbeat=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(worker, "runtime_registry", registry)
    monkeypatch.setattr(
        worker,
        "control_plane_events",
        SimpleNamespace(
            publish=AsyncMock(),
            publish_task_event=AsyncMock(),
            close=AsyncMock(),
        ),
    )
    monkeypatch.setattr(
        worker, "task_manager", SimpleNamespace(set_broadcast_hook=lambda hook: None)
    )
    monkeypatch.setattr(worker, "GENERAL_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(worker, "RELEASE_WORKER_CONCURRENCY", 0)
    monkeypatch.setattr(worker, "POLL_INTERVAL_SECONDS", 0.01)

    class Metrics:
        def __init__(self, *_):
            pass

        async def run(self, stop):
            await stop.wait()

        def heartbeat_metadata(self):
            return {}

    monkeypatch.setattr(worker, "AIWorkDispatcher", Dispatcher)
    monkeypatch.setattr(worker, "RuntimeMetricsMonitor", Metrics)
    monkeypatch.setattr(worker, "recover_expired_leases", AsyncMock(return_value=0))
    monkeypatch.setattr(
        worker, "_resume_knowledge_repairs_after_model_recovery", AsyncMock()
    )
    monkeypatch.setattr("src.services.model_dispatch_ready.publish_ready", lambda: None)
    monkeypatch.setattr(
        worker,
        "_load_runtime_routes",
        AsyncMock(
            return_value=[{"available_for_routing": True, "runtime_model_capacity": 1}]
        ),
    )
    monkeypatch.setattr(
        worker, "recover_interrupted_tasks_on_startup", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(worker, "_recover_stale_tasks", recover)
    monkeypatch.setattr(worker, "_heartbeat_claimed_task", heartbeat)
    monkeypatch.setattr(worker, "_claim_next_task_uuid", claim)
    monkeypatch.setattr(worker, "execute_task", execute)
    monkeypatch.setattr(worker, "_release_task_memory", lambda: None)
    monkeypatch.setattr(
        asyncio.get_running_loop(), "add_signal_handler", lambda *args: None
    )
    await asyncio.wait_for(worker.run_worker(), 5)
    assert completed == ["next", "parent"]
    assert not context.waiting_parent_uuids()


@pytest.mark.parametrize(
    "input_data,priority,expected",
    [
        ({"requested_by": "disease-knowledge-autopilot"}, "high", "low"),
        ({"initiated_via": "disease-knowledge-autopilot"}, "urgent", "low"),
        (
            {
                "requested_by": "disease-knowledge-autopilot",
                "initiated_via": "knowledge-source-refresh-followup",
            },
            "high",
            "low",
        ),
        (
            {
                "requested_by": "dashboard",
                "initiated_via": "knowledge-source-refresh-followup",
            },
            "high",
            "high",
        ),
        (
            {"requested_by": "operator", "initiated_via": "manual-repair"},
            "urgent",
            "urgent",
        ),
        ({}, "normal", "normal"),
    ],
)
async def test_knowledge_work_priority_uses_origin_not_repair_tag(
    input_data, priority, expected
):
    task = SimpleNamespace(
        task_uuid="priority-parent",
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        priority=priority,
        input_data=input_data,
        tags=["knowledge", "auto_repair"],
    )
    async with context.task_context(task) as state:
        assert state.priority == expected


async def test_autopilot_source_followup_persists_low_work_priority(db):
    task = fake_parent()
    task.task_type = TaskType.UPDATE_DISEASE_KNOWLEDGE
    task.input_data = {
        "requested_by": "disease-knowledge-autopilot",
        "initiated_via": "knowledge-source-refresh-followup",
    }
    async with db() as session:
        session.add(task)
        await session.commit()
    async with context.task_context(task):
        waiting = asyncio.create_task(
            client.submit_completion(fake_agent(), "autopilot prompt", None, False, {})
        )
        try:
            item = (await wait_for_items(db, 1))[0]
            assert item.priority == "low"
            report_id = await add(
                line="report_section", priority="high", parent="report-parent"
            )
            assert (await store.claim_work(route(), owner=OWNER)).id == report_id
            assert (await row(db, item.id)).status == "queued"
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_claim_does_not_materialize_entire_queued_backlog(db):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    async with db() as session:
        session.add_all(
            [
                AIWorkItem(
                    parent_task_uuid="bulk",
                    line="knowledge_brief",
                    priority="low",
                    capability="text",
                    status="queued",
                    payload={},
                )
                for _ in range(2000)
            ]
        )
        await session.commit()
    loaded = []

    def loaded_item(session, instance):
        if isinstance(instance, AIWorkItem):
            loaded.append(instance.id)

    event.listen(Session, "loaded_as_persistent", loaded_item)
    try:
        item = await store.claim_work(route(), owner=OWNER)
    finally:
        event.remove(Session, "loaded_as_persistent", loaded_item)
    assert item is not None
    assert loaded == [item.id]


@pytest.mark.parametrize("eligible_priority", ["urgent", "high"])
async def test_claim_pages_past_avoided_head_without_starving_eligible_work(
    db, eligible_priority
):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
    blocked_count = contracts.CLAIM_SCAN_PAGE_SIZE * 2 + 3
    async with db() as session:
        session.add_all(
            [
                AIWorkItem(
                    parent_task_uuid="bulk",
                    line="report_review",
                    priority="urgent",
                    capability="text",
                    status="queued",
                    payload={"avoid_models": ["first"]},
                    created_at=created,
                )
                for _ in range(blocked_count)
            ]
        )
        eligible = AIWorkItem(
            parent_task_uuid="report",
            line="report_section",
            priority=eligible_priority,
            capability="text",
            status="queued",
            payload={},
            created_at=created,
        )
        session.add(eligible)
        await session.flush()
        expected_id = eligible.id
        session.add(
            AIWorkItem(
                parent_task_uuid="later",
                line="knowledge_brief",
                priority="low",
                capability="text",
                status="queued",
                payload={},
                created_at=created,
            )
        )
        await session.commit()
    item = await store.claim_work(
        route("provider:first"),
        alternatives={"provider:first", "provider:second"},
        owner=OWNER,
    )
    assert item.id == expected_id
    other = await store.claim_work(
        route("provider:second"),
        alternatives={"provider:first", "provider:second"},
        owner=OWNER,
    )
    assert other.id != expected_id
    assert other.priority == "urgent"


async def test_claim_all_avoided_work_stays_available_for_other_route(db):
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="report_review",
        priority="high",
        payload={"avoid_models": ["first"]},
    )
    assert (
        await store.claim_work(route(), alternatives={"first", "second"}, owner=OWNER)
        is None
    )
    assert (await row(db, work_id)).status == "queued"
    assert (
        await store.claim_work(
            route("second"), alternatives={"first", "second"}, owner=OWNER
        )
    ).id == work_id


async def test_avoided_route_takes_work_when_the_other_route_is_full(db):
    # "second" is the only alternative but its single permit is busy, so an item
    # that avoids "first" must not wait for it while "first" sits idle.
    busy = await add()
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="knowledge_brief",
        priority="low",
        payload={"avoid_models": ["first"]},
    )
    release = asyncio.Event()
    served = {}

    async def routes():
        return [route("first"), route("second")]

    async def complete(item, chosen):
        served[item.id] = chosen["model_key"]
        if item.id == busy:
            await release.wait()
        return "done"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    # Pin the first claim to "second" so it is full when the avoiding item is scanned.
    first = await store.claim_work(
        route("second"), alternatives={"second"}, owner=OWNER
    )
    assert first.id == busy
    dispatcher.active[busy] = asyncio.create_task(complete(first, route("second")))
    dispatcher.active[busy]._ai_route_key = "second"
    await asyncio.sleep(0)
    await dispatcher.tick()
    release.set()
    await asyncio.gather(*dispatcher.active.values())
    assert served[work_id] == "first"


async def test_shared_dispatcher_uses_global_leases_to_detect_full_alternative(db):
    busy = await add()
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="agent_workflow",
        priority="low",
        payload={"avoid_models": ["first"]},
    )
    assert (await store.claim_work(route("second"), owner=OWNER)).id == busy
    served = {}

    async def routes():
        return [route("first"), route("second")]

    async def complete(item, chosen):
        served[item.id] = chosen["model_key"]
        return "done"

    # The second lease belongs to another dispatcher, not dispatcher.active.
    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    assert await dispatcher.tick() == 1
    await asyncio.gather(*dispatcher.active.values())
    assert served[work_id] == "first"


async def test_model_center_completer_sends_typed_routing_for_both_roles(monkeypatch):
    from src.ai import peer_review_completer as module

    seen = []

    class Agent:
        def __init__(self, *_a, **_k):
            pass

        async def complete(self, **kwargs):
            seen.append(kwargs["routing"].avoid_models)
            return "{}"

        def get_latest_conversation(self):
            return {"model": "served"}

    monkeypatch.setattr(module, "PeerReviewAgent", Agent)
    completer = module.ModelCenterCompleter(line="t", avoid_models=["m1"])
    await completer("author", "s", "p")
    await completer("reviewer", "s", "p")
    assert seen == [("m1",), ("m1", "served")]


async def make_ready(db):
    """Advance only activity backoff; keep route health and attempt budgets intact."""
    async with db() as session:
        items = (
            await session.scalars(
                select(AIWorkItem).where(AIWorkItem.status == "queued")
            )
        ).all()
        for item in items:
            item.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()


async def test_shutdown_releases_only_owned_leases(db):
    first_id, second_id = await add(), await add()
    first = await store.claim_work(route(), owner="executor-a")
    second = await store.claim_work(route("second"), owner="executor-b")
    assert {first.id, second.id} == {first_id, second_id}
    assert await store.release_owned_leases("executor-a") == 1
    released, retained = await row(db, first.id), await row(db, second.id)
    assert (
        released.status,
        released.attempt,
        released.lease_owner,
        released.lease_token,
    ) == ("queued", 1, None, None)
    assert (retained.status, retained.lease_owner, retained.lease_token) == (
        "leased",
        "executor-b",
        second.lease_token,
    )
    assert await store.release_owned_leases("executor-a") == 0
    assert await store.claim_work(route("second"), owner="executor-a") is None


async def test_renewal_keeps_owner_and_token_and_never_extends_execution_deadline(db):
    await add()
    now = datetime.now(timezone.utc)
    claimed = await store.claim_work(route(), owner=OWNER, now=now)
    renewal = now + timedelta(seconds=contracts.LEASE_SECONDS - 1)
    assert await store.renew_owned_leases("another-executor", now=renewal) == set()
    assert await store.renew_owned_leases(OWNER, now=renewal) == {claimed.id}
    current = await row(db, claimed.id)
    assert current.lease_token == claimed.lease_token

    assert current.deadline_at == claimed.deadline_at.replace(tzinfo=None)
    assert store._utc(current.lease_expires_at) > store._utc(claimed.lease_expires_at)
    assert (
        await store.recover_expired_leases(
            now=now + timedelta(seconds=contracts.LEASE_SECONDS + 1)
        )
        == 0
    )
    assert (
        await store.renew_owned_leases(
            OWNER, now=now + timedelta(seconds=contracts.EXECUTION_DEADLINE_SECONDS + 1)
        )
        == set()
    )


async def test_deleted_parent_prevents_a_claimed_activity_from_sending_provider_io(db):
    work_id = await add()
    claimed = await store.claim_work(route(), owner=OWNER)
    async with db() as session:
        parent = await session.scalar(select(Task).where(Task.task_uuid == "parent"))
        await session.execute(delete(Task).where(Task.id == parent.id))
        await session.commit()

    async def complete(item, chosen):
        pytest.fail("An orphan activity must not call a provider")

    dispatcher = dispatch.AIWorkDispatcher(complete=complete, owner=OWNER)
    await dispatcher._execute(claimed, route())
    assert (await row(db, work_id)).status == "cancelled"
    assert not dispatcher.outcomes


async def test_deleted_parent_cancels_an_existing_provider_execution(db):
    work_id = await add()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher.tick()
    await asyncio.wait_for(started.wait(), 1)
    async with db() as session:
        parent = await session.scalar(select(Task).where(Task.task_uuid == "parent"))
        await session.execute(delete(Task).where(Task.id == parent.id))
        await session.commit()
    await dispatcher.tick()
    assert stopped.is_set()
    assert (await row(db, work_id)).status == "cancelled"


async def test_background_recovery_probe_does_not_require_a_business_parent(db):
    work_id = await store.enqueue_runtime_probe()
    claimed = await store.claim_probe(owner=OWNER)

    async def complete(item, chosen):
        return {"operation_result": {"success": True}}

    dispatcher = dispatch.AIWorkDispatcher(complete=complete, owner=OWNER)
    await dispatcher._execute(claimed, {"model_key": "health:probe"})
    assert (await row(db, work_id)).status == "succeeded"


async def test_quota_failures_consume_the_same_bounded_attempt_budget(db):
    work_id = await add()
    calls = []

    async def routes():
        return [route()]

    async def complete(item, chosen):
        calls.append(item.attempt)
        raise RuntimeError("HTTP 429 rate limited")

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    for _ in range(4):
        await make_ready(db)
        await dispatcher.tick()
        await asyncio.gather(*dispatcher.active.values())
    assert calls == [1, 2, 3]
    final = await row(db, work_id)
    assert (final.status, final.attempt) == ("failed", 3)


async def test_unknown_application_error_does_not_repeat_provider_execution(db):
    work_id = await add()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        raise ValueError("invalid response contract")

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    final = await row(db, work_id)
    assert (final.status, final.attempt) == ("failed", 1)


async def test_generation_settings_are_part_of_activity_identity(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    calls = []

    async def routes():
        return [route()]

    async def complete(item, chosen):
        calls.append(item.id)
        return {
            "text": str(item.payload["max_tokens"]),
            "conversation": {"model": "selected"},
            "input_mode": "text",
        }

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(task):
        first_agent = fake_agent(max_tokens=100)
        pending = asyncio.create_task(
            client.submit_completion(first_agent, "identity", None, False, {})
        )
        await wait_for_items(db, 1)
        await dispatcher.tick()
        assert await asyncio.wait_for(pending, 3) == "100"
        assert first_agent.conversation_history == [{"model": "selected"}]
        assert first_agent._last_input_mode == "text"
        changed = asyncio.create_task(
            client.submit_completion(
                fake_agent(max_tokens=200), "identity", None, False, {}
            )
        )
        await wait_for_items(db, 2)
        await dispatcher.tick()
        assert await asyncio.wait_for(changed, 3) == "200"
    assert len(set(calls)) == 2


async def test_terminal_result_cannot_replay_after_parent_cancellation(db):
    from src.services.exceptions import TaskCancelledError

    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        return "completed before cancellation"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(task):
        pending = asyncio.create_task(
            client.submit_completion(fake_agent(), "result", None, False, {})
        )
        await wait_for_items(db, 1)
        await dispatcher.tick()
        assert await asyncio.wait_for(pending, 3) == "completed before cancellation"
        async with db() as session:
            parent = await session.scalar(select(Task))
            parent.status = TaskStatus.CANCELLED
            await session.commit()
        with pytest.raises(TaskCancelledError):
            await client.submit_completion(fake_agent(), "result", None, False, {})


async def test_expired_execution_finishes_cancellation_before_it_can_be_reclaimed(
    db, monkeypatch
):
    work_id = await add()
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(store, "_now", lambda: clock)
    monkeypatch.setattr(dispatch, "_now", lambda: clock)
    started, cleaning_up, allow_cleanup = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    attempts = []

    async def routes():
        return [route()]

    async def complete(item, chosen):
        attempts.append(item.attempt)
        if item.attempt == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning_up.set()
                await allow_cleanup.wait()
        return "recovered"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher.tick()
    await asyncio.wait_for(started.wait(), 1)
    clock += timedelta(seconds=contracts.LEASE_SECONDS + 1)
    ticking = asyncio.create_task(dispatcher.tick())
    try:
        await asyncio.wait_for(cleaning_up.wait(), 1)
        assert attempts == [1]
        assert (await row(db, work_id)).status == "leased"
    finally:
        allow_cleanup.set()
        await ticking
        await asyncio.gather(*dispatcher.active.values())
    assert attempts == [1, 2]
    assert (await row(db, work_id)).result["text"] == "recovered"


async def test_another_dispatcher_cannot_execute_a_claimed_activity(db):
    work_id = await add()
    claimed = await store.claim_work(route(), owner="owner-a")

    async def complete(item, chosen):
        pytest.fail("A dispatcher must not send another owner's provider request")

    dispatcher = dispatch.AIWorkDispatcher(complete=complete, owner="owner-b")
    await dispatcher._execute(claimed, route())
    assert (await row(db, work_id)).status == "leased"
    assert not dispatcher.outcomes


async def test_cancellation_committed_while_loading_terminal_result_wins(monkeypatch):
    from unittest.mock import AsyncMock

    from src.services.exceptions import TaskCancelledError

    parent_status = TaskStatus.RUNNING

    class DB:
        async def get(self, model, work_id):
            nonlocal parent_status
            parent_status = TaskStatus.CANCELLED
            return SimpleNamespace(
                status="succeeded", result={"text": "must not escape"}, error=None
            )

        async def scalar(self, statement):
            return SimpleNamespace(status=parent_status, metadata_={})

    @asynccontextmanager
    async def database():
        yield DB()

    @asynccontextmanager
    async def subscribe(*args):
        yield asyncio.Event()

    monkeypatch.setattr(client, "get_database", database)
    monkeypatch.setattr(client, "_set_waiting", AsyncMock())
    monkeypatch.setattr(
        client, "activity_signals", lambda db: SimpleNamespace(subscribe=subscribe)
    )
    state = context.ParentContext("parent", "knowledge_brief", "low")
    with pytest.raises(TaskCancelledError):
        await client._await_result(1, state)
    assert state.waiting == 0


async def test_activity_notifications_target_only_subscribers_and_reconcile_lost_hints(
    db,
):
    first, second = await add(parent="one"), await add(parent="two")
    hub = signals.ActivitySignals(store.get_database, interval=0.02)
    async with (
        hub.subscribe([first], "one") as first_event,
        hub.subscribe([second], "two") as second_event,
    ):
        hub.notify([first])
        assert first_event.is_set()
        assert not second_event.is_set()
        async with db() as session:
            item = await session.get(AIWorkItem, second)
            item.status = "succeeded"
            await session.commit()
        # SQLite sends no LISTEN notification; reconciliation still releases the waiter.
        await asyncio.wait_for(second_event.wait(), 1)


@pytest.mark.parametrize(
    ("key", "patterns", "expected"),
    [
        ("A:Model-X", ["model-x"], True),
        ("A:Model-X", ["a:model-x"], True),
        ("B:Model-X", ["a:model-x"], False),
        ("model-x", ["a:model-x"], False),
        ("A:Model-X", [""], False),
    ],
)
def test_route_constraints_share_model_identity(key, patterns, expected):
    from src.ai.model_identity import model_matches

    assert model_matches(key, patterns) is expected


async def test_strict_bare_model_exclusion_applies_across_provider_case(db):
    work_id = await enqueue(
        parent_task_uuid="parent",
        line="report_review",
        priority="high",
        payload={"avoid_models": ["model-x"], "require_distinct_model": True},
    )
    assert await store.claim_work(route("ProviderB:Model-X"), owner=OWNER) is None
    assert (
        await store.claim_work(route("ProviderB:Model-Y"), owner=OWNER)
    ).id == work_id


async def test_cancellation_during_a_failing_provider_call_does_not_retry(db):
    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()
    work_id = await add()
    claimed = await store.claim_work(route(), owner=OWNER)

    async def complete(item, chosen):
        async with db() as session:
            task = await session.scalar(select(Task))
            task.status = TaskStatus.CANCELLED
            await session.commit()
        raise TimeoutError("provider failed after parent cancellation")

    dispatcher = dispatch.AIWorkDispatcher(complete=complete, owner=OWNER)
    await dispatcher._execute(claimed, route())
    current = await row(db, work_id)
    assert (current.status, current.attempt, current.error) == ("cancelled", 1, None)
    assert current.lease_token is None
    assert not dispatcher.outcomes


async def test_queue_age_boundary_expires_waiters_but_does_not_abort_leased_execution(
    db,
):
    leased_id = await add()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=contracts.QUEUE_WAIT_SECONDS)
    leased = await store.claim_work(route(), owner=OWNER, now=now)
    expired_id, fresh_id = await add(), await add()
    async with db() as session:
        expired = await session.get(AIWorkItem, expired_id)
        expired.created_at = cutoff
        fresh = await session.get(AIWorkItem, fresh_id)
        fresh.created_at = cutoff + timedelta(microseconds=1)
        fresh.available_at = now
        active = await session.get(AIWorkItem, leased_id)
        active.created_at = cutoff - timedelta(seconds=1)
        await session.commit()
    # Claiming filters expired work even between the periodic expiry sweeps.
    assert (
        await store.claim_work(route("second"), owner=OWNER, now=now)
    ).id == fresh_id
    assert await store.expire_queued_activities(now=now) == 1
    expired, active = await row(db, expired_id), await row(db, leased_id)
    assert (expired.status, expired.attempt) == ("failed", 1)
    assert "maximum queue age" in expired.error
    assert active.status == "leased" and active.lease_token == leased.lease_token


async def test_strict_unavailable_model_requirement_eventually_finishes_parent_wait(db):
    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()

    async def routes():
        return [route("model-x")]

    async def complete(item, chosen):
        pytest.fail("A strict reviewer cannot use its excluded author model")

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(parent):
        pending = asyncio.create_task(
            client.submit_completion(
                fake_agent(),
                "strict review",
                None,
                False,
                {
                    "routing": contracts.RoutingRequirements(
                        avoid_models=("model-x",), require_distinct_model=True
                    )
                },
            )
        )
        items = await wait_for_items(db, 1)
        async with db() as session:
            item = await session.get(AIWorkItem, items[0].id)
            item.created_at = datetime.now(timezone.utc) - timedelta(
                seconds=contracts.QUEUE_WAIT_SECONDS + 1
            )
            await session.commit()
        assert await dispatcher.tick() == 0
        with pytest.raises(contracts.AIWorkFailed, match="maximum queue age"):
            await asyncio.wait_for(pending, 2)
    assert not context.waiting_parent_uuids()


async def test_health_activity_queue_age_is_also_bounded(db):
    now = datetime.now(timezone.utc)
    probe_id = await store.enqueue_runtime_probe()
    async with db() as session:
        item = await session.get(AIWorkItem, probe_id)
        item.created_at = now - timedelta(seconds=contracts.QUEUE_WAIT_SECONDS + 1)
        await session.commit()
    assert await store.claim_probe(owner=OWNER) is None
    assert await store.expire_queued_activities(now=now) == 1
    assert (await row(db, probe_id)).status == "failed"


async def test_queue_expiry_maintenance_is_throttled_between_dispatch_scans(
    db, monkeypatch
):
    from unittest.mock import AsyncMock

    expire = AsyncMock(return_value=0)
    monkeypatch.setattr(dispatch, "expire_queued_activities", expire)
    dispatcher = dispatch.AIWorkDispatcher(
        route_loader=AsyncMock(return_value=[]), owner=OWNER
    )
    await dispatcher.tick()
    await dispatcher.tick()
    expire.assert_awaited_once()
    dispatcher._queue_checked_at -= 21
    await dispatcher.tick()
    assert expire.await_count == 2


@pytest.mark.parametrize("source", ["activity", "parent"])
async def test_cancellation_holds_route_permit_until_provider_cleanup_finishes(
    db, source
):
    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()
    first_id = await add()
    second_id = await add(parent="other-parent")
    started, cleaning_up, allow_cleanup = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    calls = []

    async def routes():
        return [route()]

    async def complete(item, chosen):
        calls.append(item.id)
        if item.id == first_id:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning_up.set()
                await allow_cleanup.wait()
        return "second response"

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    await dispatcher.tick()
    await asyncio.wait_for(started.wait(), 1)
    original = await row(db, first_id)
    async with db() as session:
        if source == "activity":
            await store.request_cancellation(
                session, await session.get(AIWorkItem, first_id)
            )
        else:
            task = await session.scalar(select(Task))
            task.status = TaskStatus.CANCELLED
        await session.commit()
    assert await store.claim_work(route(), owner="another-executor") is None
    ticking = asyncio.create_task(dispatcher.tick())
    try:
        await asyncio.wait_for(cleaning_up.wait(), 1)
        current = await row(db, first_id)
        assert current.status == "leased" and current.cancel_requested
        assert current.lease_token == original.lease_token
        assert await store.active_route_lease_counts() == {"first": 1}
        assert await store.claim_work(route(), owner="another-executor") is None
        assert calls == [first_id]
    finally:
        allow_cleanup.set()
        await ticking
        await asyncio.gather(*dispatcher.active.values())
    assert (await row(db, first_id)).status == "cancelled"
    assert (await row(db, first_id)).lease_token is None
    assert (await row(db, second_id)).status == "succeeded"
    assert calls == [first_id, second_id]


async def test_cancelled_client_leaves_running_provider_permit_with_its_executor(db):
    from src.services.exceptions import TaskCancelledError

    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()
    started = asyncio.Event()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        started.set()
        await asyncio.Event().wait()

    dispatcher = dispatch.AIWorkDispatcher(routes, complete, owner=OWNER)
    async with context.task_context(parent):
        pending = asyncio.create_task(
            client.submit_completion(
                fake_agent(), "cancel running call", None, False, {}
            )
        )
        items = await wait_for_items(db, 1)
        await dispatcher.tick()
        await asyncio.wait_for(started.wait(), 1)
        async with db() as session:
            task = await session.scalar(select(Task))
            task.status = TaskStatus.CANCELLED
            await session.commit()
        with pytest.raises(TaskCancelledError):
            await asyncio.wait_for(pending, 2)
        current = await row(db, items[0].id)
        assert current.cancel_requested and current.status == "leased"
        assert await store.active_route_lease_counts() == {"first": 1}
        await dispatcher.tick()
        assert (await row(db, items[0].id)).status == "cancelled"
        assert await store.active_route_lease_counts() == {}


@pytest.mark.parametrize("recover", [False, True])
async def test_cancel_requested_lease_is_never_requeued_by_shutdown_or_recovery(
    db, recover
):
    work_id = await add()
    claimed = await store.claim_work(route(), owner=OWNER)
    async with db() as session:
        await store.request_cancellation(
            session, await session.get(AIWorkItem, work_id)
        )
        await session.commit()
    if recover:
        await store.recover_expired_leases(
            now=store._utc(claimed.lease_expires_at) + timedelta(seconds=1)
        )
    else:
        await store.release_owned_leases(OWNER)
    current = await row(db, work_id)
    assert (current.status, current.attempt) == ("cancelled", 1)
    assert current.lease_token is None


async def test_cancellation_refreshes_a_queued_row_that_was_just_leased_elsewhere(db):
    work_id = await add()
    async with db() as session:
        stale = await session.get(AIWorkItem, work_id)
        claimed = await store.claim_work(route(), owner="other-executor")
        assert stale.status == "queued"
        await store.request_cancellation(session, stale)
        await session.commit()
    current = await row(db, work_id)
    assert current.status == "leased" and current.cancel_requested
    assert current.lease_token == claimed.lease_token


async def test_explicit_parent_retry_creates_new_activity_after_failure(
    db, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.control_plane import operations

    monkeypatch.setattr(
        operations.control_plane_events, "publish_task_event", AsyncMock()
    )
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    async with context.task_context(task) as first_state:
        pending = asyncio.create_task(
            client.submit_completion(fake_agent(), "retry", None, True, {})
        )
        original = (await wait_for_items(db, 1))[0]
        async with db() as session:
            item = await session.get(AIWorkItem, original.id)
            item.status, item.error = "failed", "upstream exhausted retries"
            await session.commit()
        with pytest.raises(contracts.AIWorkFailed):
            await asyncio.wait_for(pending, 2)
        assert first_state.generation == 0
    async with db() as session:
        parent = await session.get(Task, task.id)
        parent.status = TaskStatus.FAILED
        await session.commit()
        await operations.TaskOperationsService(session).retry(task.task_uuid)
        refreshed = await session.get(Task, task.id)
        assert refreshed.metadata_["ai_execution_generation"] == 1
    async with context.task_context(refreshed) as second_state:
        pending = asyncio.create_task(
            client.submit_completion(fake_agent(), "retry", None, True, {})
        )
        items = await wait_for_items(db, 2)
        replacement = next(item for item in items if item.id != original.id)
        assert replacement.payload["execution_generation"] == 1
        assert replacement.payload["invocation"] == 0
        assert replacement.payload["use_cache"] is False

        async def complete(item, chosen):
            assert item.id == replacement.id
            return "fresh answer"

        dispatcher = dispatch.AIWorkDispatcher(
            lambda: asyncio.sleep(0, result=[route()]), complete, owner=OWNER
        )
        await dispatcher.tick()
        assert await asyncio.wait_for(pending, 2) == "fresh answer"
        assert second_state.generation == 1
    assert (await row(db, original.id)).status == "failed"


async def test_repeat_prompt_creates_new_attempt_but_restart_replays_each_ordinal(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    calls = []

    async def complete(item, chosen):
        calls.append(item.id)
        ordinal = item.payload["invocation"]
        assert item.payload["use_cache"] is (ordinal == 0)
        return "invalid JSON" if ordinal == 0 else '{"fixed":true}'

    dispatcher = dispatch.AIWorkDispatcher(
        lambda: asyncio.sleep(0, result=[route()]), complete, owner=OWNER
    )
    expected = ["invalid JSON", '{"fixed":true}']
    async with context.task_context(task):
        for ordinal, answer in enumerate(expected):
            pending = asyncio.create_task(
                client.submit_completion(
                    fake_agent(), "same repair prompt", None, True, {}
                )
            )
            await wait_for_items(db, ordinal + 1)
            await dispatcher.tick()
            assert await asyncio.wait_for(pending, 2) == answer
    async with context.task_context(task):
        for answer in expected:
            assert (
                await asyncio.wait_for(
                    client.submit_completion(
                        fake_agent(), "same repair prompt", None, True, {}
                    ),
                    2,
                )
                == answer
            )
    assert len(calls) == 2
    async with db() as session:
        items = list((await session.scalars(select(AIWorkItem))).all())
    assert len(items) == 2 and sorted(item.payload["invocation"] for item in items) == [
        0,
        1,
    ]


async def test_retry_cancels_old_work_without_releasing_live_http_permit(
    db, monkeypatch
):
    from unittest.mock import AsyncMock

    from src.control_plane import operations

    monkeypatch.setattr(
        operations.control_plane_events, "publish_task_event", AsyncMock()
    )
    first, second = await add(), await add()
    leased = await store.claim_work(route(), owner=OWNER)
    assert leased.id == first
    async with db() as session:
        parent = await session.scalar(select(Task).where(Task.task_uuid == "parent"))
        parent.status = TaskStatus.CANCELLED
        parent.metadata_ = {"cancel_requested": True}
        await session.commit()
        await operations.TaskOperationsService(session).retry("parent")
    active, queued = await row(db, leased.id), await row(db, second)
    assert active.status == "leased" and active.cancel_requested is True
    assert active.lease_token == leased.lease_token
    assert queued.status == "cancelled"
    assert await store.claim_work(route(), owner="other-executor") is None


async def test_old_execution_cannot_submit_consume_or_clear_new_waiting_state(db):
    from src.services.exceptions import TaskExecutionSupersededError

    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    async with context.task_context(task) as old:
        await context._set_waiting(old, True)
        async with db() as session:
            parent = await session.get(Task, task.id)
            parent.metadata_ = {"ai_execution_generation": 1}
            parent.input_data = {"ai_waiting": True}
            await session.commit()
        new = context.ParentContext("parent", "report_section", "high", generation=1)
        await context._set_waiting(new, True)
        await context._set_waiting(old, False)
        async with db() as session:
            parent = await session.get(Task, task.id)
            assert parent.input_data["ai_waiting"] is True
        assert context.waiting_parent_uuids() == {"parent"}
        with pytest.raises(TaskExecutionSupersededError):
            await client.submit_completion(fake_agent(), "obsolete", None, False, {})
        with pytest.raises(TaskExecutionSupersededError):
            await client._await_result(999, old)
        assert context.waiting_parent_uuids() == {"parent"}
        await context._set_waiting(new, False)


async def test_batch_submission_preserves_order_and_reuses_existing_and_duplicate_keys(
    db, monkeypatch
):
    from unittest.mock import AsyncMock

    async with store.get_database() as session:
        existing = await store.submit_activity(
            session,
            parent_task_uuid="parent",
            line="report_section",
            priority="high",
            payload={"request_key": "existing", "original": True},
        )
        existing.status = "failed"
        existing_id = existing.id
    notify = AsyncMock()
    monkeypatch.setattr(store, "publish_changes", notify)
    async with store.get_database() as session:
        payloads = [
            {"request_key": "new-a"},
            {"request_key": "existing", "replacement": True},
            {"request_key": "new-b"},
            {"request_key": "new-a"},
            {"request_key": "existing"},
        ]
        items = await store.submit_activities(
            session,
            parent_task_uuid="parent",
            line="literature_enrich",
            priority="low",
            payloads=payloads,
        )
        assert [item.payload["request_key"] for item in items] == [
            "new-a",
            "existing",
            "new-b",
            "new-a",
            "existing",
        ]
        assert items[0] is items[3] and items[1] is items[4]
        assert items[1].id == existing_id and items[1].status == "failed"
        assert items[1].payload == {"request_key": "existing", "original": True}
        assert items[0].line == "literature_enrich" and items[0].priority == "low"
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["work_ids"] == [items[0].id, items[2].id]
    async with db() as session:
        assert len((await session.scalars(select(AIWorkItem))).all()) == 3


async def test_batch_submission_key_scope_is_per_parent_and_unkeyed_entries_are_distinct(
    db,
):
    async with store.get_database() as session:
        first = await store.submit_activities(
            session,
            parent_task_uuid="first",
            line="report_section",
            priority="high",
            payloads=[{"request_key": "shared"}, {}, {}, {"request_key": ""}],
        )
        other = await store.submit_activities(
            session,
            parent_task_uuid="second",
            line="report_section",
            priority="high",
            payloads=[{"request_key": "shared"}],
        )
        assert len({item.id for item in [*first, *other]}) == 5
        replay = await store.submit_activity(
            session,
            parent_task_uuid="first",
            line="report_section",
            priority="high",
            payload={"request_key": "shared"},
        )
        assert replay is first[0]


async def test_batch_submission_rolls_back_with_its_parent_transaction(db):
    parent = fake_parent()
    async with db() as session:
        session.add(parent)
        await session.commit()
    with pytest.raises(RuntimeError, match="abort batch"):
        async with store.get_database() as session:
            stored = await session.scalar(
                select(Task).where(Task.task_uuid == "parent")
            )
            stored.input_data = {"submitted": True}
            items = await store.submit_activities(
                session,
                parent_task_uuid="parent",
                line="report_section",
                priority="high",
                payloads=[{"request_key": f"rollback-{i}"} for i in range(8)],
            )
            assert all(item.id is not None for item in items)
            raise RuntimeError("abort batch")
    async with db() as session:
        assert await session.scalar(select(AIWorkItem.id)) is None
        assert (await session.scalar(select(Task))).input_data == {}


async def test_empty_and_replayed_batches_do_not_flush_or_notify(db, monkeypatch):
    from unittest.mock import AsyncMock

    async with store.get_database() as session:
        await store.submit_activity(
            session,
            parent_task_uuid="parent",
            line="report_section",
            priority="high",
            payload={"request_key": "replay"},
        )
    notify = AsyncMock()
    monkeypatch.setattr(store, "publish_changes", notify)
    async with store.get_database() as session:
        flush = AsyncMock(wraps=session.flush)
        monkeypatch.setattr(session, "flush", flush)
        assert (
            await store.submit_activities(
                session,
                parent_task_uuid="parent",
                line="report_section",
                priority="high",
                payloads=[],
            )
            == []
        )
        items = await store.submit_activities(
            session,
            parent_task_uuid="parent",
            line="report_section",
            priority="high",
            payloads=[{"request_key": "replay"}] * 3,
        )
        assert items[0] is items[1] is items[2]
        flush.assert_not_awaited()
        notify.assert_not_awaited()
