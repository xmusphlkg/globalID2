"""Model work priority, route capacity, leases, restart replay and waiting slots."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("aiosqlite")
from src.ai import work_queue as queue
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

    monkeypatch.setattr(queue, "get_database", database)
    monkeypatch.setattr(queue, "ROOT", tmp_path)
    yield maker
    queue._WAITING.clear()
    await engine.dispose()


def route(key="first", capacity=1, available=True):
    return {
        "model_key": key,
        "model_name": key,
        "runtime_model_capacity": capacity,
        "available_for_routing": available,
    }


async def add(line="knowledge_brief", priority="low", parent="parent"):
    return await queue.enqueue_work(
        parent_task_uuid=parent, line=line, priority=priority, payload={"sample": True}
    )


async def row(db, work_id):
    async with db() as session:
        return await session.get(AIWorkItem, work_id)


async def test_high_priority_report_claimed_before_low_backlog(db):
    low = await add()
    high = await add("report_section", "high")
    item = await queue.claim_work(route())
    assert item.id == high
    assert (await row(db, low)).status == "queued"
    assert await queue.claim_work(route()) is None  # route permit is held
    assert (await queue.claim_work(route("second"))).id == low


async def test_cooling_route_leaves_item_queued_for_other_route(db):
    work_id = await add()
    assert await queue.claim_work(route(available=False)) is None
    assert (await row(db, work_id)).status == "queued"
    assert (await queue.claim_work(route("second"))).id == work_id


async def test_no_routable_routes_never_fail_parent_or_work(db):
    work_id = await add()

    async def routes():
        return [route(available=False), route("second", available=False)]

    dispatcher = queue.AIWorkDispatcher(route_loader=routes)
    assert await dispatcher.tick() == 0
    assert (await row(db, work_id)).status == "queued"
    assert (await row(db, work_id)).attempt == 1


async def test_expired_lease_reuses_id_and_attempt_is_bounded(db):
    work_id = await add()
    now = datetime.now(timezone.utc)
    for attempt in (1, 2, 3):
        leased = await queue.claim_work(route(), now=now)
        assert leased.id == work_id
        assert leased.attempt == attempt
        now += timedelta(seconds=queue.LEASE_SECONDS + 1)
        assert await queue.recover_expired_leases(now=now) == 1
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
        assert queue.in_dispatcher()
        calls.append(chosen["model_key"])
        if len(calls) == 1:
            chosen["available_for_routing"] = False
            raise RuntimeError("HTTP 429 rate limited")
        return "done"

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    item = await row(db, work_id)
    assert item.status == "queued" and item.attempt == 1
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    assert (await row(db, work_id)).status == "succeeded"
    assert calls[0] != calls[1]


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

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    async with queue.task_context(task):
        waiting = asyncio.create_task(
            queue.submit_completion(agent, "prompt", "system", False, {})
        )
        for _ in range(100):
            if "parent" in queue.waiting_parent_uuids():
                break
            await asyncio.sleep(0.01)
        assert "parent" in queue.waiting_parent_uuids()
        async with db() as session:
            stored = (await session.execute(select(Task))).scalar_one()
            assert stored.status == TaskStatus.RUNNING
            assert stored.input_data["ai_waiting"] is True
        await dispatcher.tick()
        assert await asyncio.wait_for(waiting, 2) == "result"
    assert not queue.waiting_parent_uuids()
    # A recovered parent replays a committed response instead of calling twice.
    async with queue.task_context(task):
        assert (
            await queue.submit_completion(agent, "prompt", "system", False, {})
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
        async with queue.task_context(task) as state:
            state.failures.append("one article failed")
            if strict:
                with pytest.raises(queue.AIWorkFailed):
                    queue.assert_children_succeeded()
            else:
                queue.assert_children_succeeded()


async def test_health_check_runs_in_dispatcher_even_when_business_routes_cool(
    db, monkeypatch
):
    from src.ai import model_center

    called = []

    async def check(model_id, *, structured):
        called.append((model_id, structured, queue.in_dispatcher()))
        return {"success": True, "model_id": model_id}

    monkeypatch.setattr(model_center, "check_model_by_id", check)

    async def routes():
        return [route(available=False)]

    work_id = await queue.enqueue_work(
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
    dispatcher = queue.AIWorkDispatcher(route_loader=routes)
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
    assert (await queue.claim_work(route())).id == urgent
    assert (await queue.claim_work(route("second"))).id == high
    assert (await row(db, future)).status == "queued"


@pytest.mark.parametrize("capacity", [1, 2])
async def test_concurrent_claims_respect_route_capacity(db, capacity):
    for _ in range(4):
        await add()
    claims = await asyncio.gather(
        *(queue.claim_work(route(capacity=capacity)) for _ in range(4))
    )
    ids = [item.id for item in claims if item is not None]
    assert len(ids) == len(set(ids)) == capacity


async def test_two_routes_cannot_lease_the_same_item(db):
    work_id = await add()
    claims = await asyncio.gather(
        queue.claim_work(route()), queue.claim_work(route("second"))
    )
    assert [item.id for item in claims if item is not None] == [work_id]


async def test_health_permit_is_atomic_across_dispatchers(db):
    for _ in range(2):
        await queue.enqueue_work(
            parent_task_uuid="parent",
            line="model_probe",
            priority="high",
            capability="health",
            payload={},
        )
    claims = await asyncio.gather(queue.claim_probe(), queue.claim_probe())
    assert sum(item is not None for item in claims) == 1


async def test_failed_agent_workflow_call_updates_progress_and_keeps_the_error(db):
    from src.domain import TaskWorkbook

    async with db() as session:
        connection = await session.connection()
        await connection.run_sync(lambda sync: TaskWorkbook.__table__.create(sync, checkfirst=True))
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
        raise ValueError("invalid model response")

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    for _ in range(3):
        await dispatcher.tick()
        await asyncio.gather(*dispatcher.active.values())
    async with db() as session:
        task = (await session.execute(select(Task).where(Task.task_uuid == "parent-log"))).scalar_one()
        logs = (await session.execute(select(TaskWorkbook).order_by(TaskWorkbook.id))).scalars().all()
    assert task.progress == 99
    assert [log.title for log in logs] == ["Model call retry", "Model call retry", "Model call failed"]
    assert "invalid model response" in logs[-1].content
    assert (await row(db, work_id)).status == "failed"


async def test_non_cooling_failures_stop_after_three_attempts(db):
    work_id = await add()

    async def routes():
        return [route()]

    attempts = []

    async def complete(item, chosen):
        attempts.append(item.attempt)
        raise ValueError("invalid model response")

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    for _ in range(4):
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
        raise ValueError("invalid response")

    item = await queue.claim_work(route())
    dispatcher = queue.AIWorkDispatcher(routes, complete)
    await dispatcher._execute(item, route())
    item = await row(db, work_id)
    assert (item.status, item.attempt) == ("queued", 2)


async def test_wrapped_cooldown_at_third_attempt_can_switch_routes_without_failing(db):
    work_id = await add()
    async with db() as session:
        item = await session.get(AIWorkItem, work_id)
        item.attempt = 3
        await session.commit()
    live = [route(), route("second")]

    async def routes():
        return live

    async def complete(item, chosen):
        if chosen["model_key"] == "first":
            live[0]["available_for_routing"] = False
            raise RuntimeError("Agent completion failed")
        return "healthy route response"

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    first = await queue.claim_work(live[0])
    await dispatcher._execute(first, live[0])
    waiting = await row(db, work_id)
    assert (waiting.status, waiting.attempt) == ("queued", 3)
    assert await dispatcher.tick() == 1
    await asyncio.gather(*dispatcher.active.values())
    result = await row(db, work_id)
    assert (result.status, result.attempt, result.route_key) == (
        "succeeded",
        3,
        "second",
    )


async def test_concurrent_recovery_does_not_advance_same_lease_twice(db):
    work_id = await add()
    now = datetime.now(timezone.utc)
    await queue.claim_work(route(), now=now)
    now += timedelta(seconds=queue.LEASE_SECONDS + 1)
    recovered = await asyncio.gather(
        queue.recover_expired_leases(now=now), queue.recover_expired_leases(now=now)
    )
    assert sum(recovered) == 1
    item = await row(db, work_id)
    assert (item.status, item.attempt) == ("queued", 2)


@pytest.mark.parametrize("fails", [False, True])
async def test_late_callback_cannot_overwrite_new_lease_on_same_attempt(db, fails):
    work_id = await add()
    old = await queue.claim_work(route())
    async with db() as session:
        current = await session.get(AIWorkItem, work_id)
        # A cooldown keeps attempt=1, and the route can recover before a late
        # provider callback arrives. The lease boundary must fence that callback.
        current.lease_expires_at += timedelta(seconds=5)
        await session.commit()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        if fails:
            raise RuntimeError("HTTP 429 from obsolete call")
        return "obsolete response"

    await queue.AIWorkDispatcher(routes, complete)._execute(old, route())
    current = await row(db, work_id)
    assert (current.status, current.attempt, current.route_key) == (
        "leased",
        1,
        "first",
    )
    assert current.result is None and current.error is None


async def test_dispatcher_cancels_own_expired_call_before_reclaiming(db, monkeypatch):
    work_id = await add()
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(queue, "_now", lambda: clock)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        if item.attempt == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return "recovered result"

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    await dispatcher.tick()
    await asyncio.wait_for(started.wait(), 2)
    clock += timedelta(seconds=queue.LEASE_SECONDS + 1)
    await dispatcher.tick()
    await asyncio.gather(*dispatcher.active.values())
    assert cancelled.is_set()
    current = await row(db, work_id)
    assert (current.status, current.attempt) == ("succeeded", 2)
    assert current.result["text"] == "recovered result"


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
            if len(items) >= count and "parent" in queue.waiting_parent_uuids():
                return items
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), 3)


async def test_identical_concurrent_submissions_share_one_durable_call(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    calls = []

    async def routes():
        return [route()]

    async def complete(item, chosen):
        calls.append(item.id)
        return "shared"

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    async with queue.task_context(task) as state:
        waiting = [
            asyncio.create_task(
                queue.submit_completion(fake_agent(), "same", None, False, {})
            )
            for _ in range(2)
        ]
        try:
            await wait_for_items(db, 1)
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
    assert len(calls) == 1
    async with db() as session:
        assert len(list((await session.execute(select(AIWorkItem))).scalars())) == 1
    assert not queue.waiting_parent_uuids()


async def test_restarted_parent_attaches_to_pending_work(db):
    task = fake_parent()
    async with db() as session:
        session.add(task)
        await session.commit()
    async with queue.task_context(task):
        waiting = asyncio.create_task(
            queue.submit_completion(fake_agent(), "pending", None, False, {})
        )
        original = (await wait_for_items(db, 1))[0]
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    assert not queue.waiting_parent_uuids()

    async def routes():
        return [route()]

    async def complete(item, chosen):
        assert item.id == original.id
        return "resumed"

    async with queue.task_context(task):
        waiting = asyncio.create_task(
            queue.submit_completion(fake_agent(), "pending", None, False, {})
        )
        try:
            items = await wait_for_items(db, 1)
            assert [item.id for item in items] == [original.id]
            await queue.AIWorkDispatcher(routes, complete).tick()
            assert await asyncio.wait_for(waiting, 3) == "resumed"
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_legacy_completed_result_replays_but_changed_settings_create_work(db):
    task = fake_parent()
    path, digest = queue._write_request(
        task.task_uuid, {"prompt": "legacy", "system": None, "kwargs": {}}
    )
    history = {"model": "legacy-model", "response": "legacy result"}
    async with db() as session:
        session.add(task)
        session.add(
            AIWorkItem(
                parent_task_uuid="parent",
                line="report_section",
                priority="high",
                status="succeeded",
                payload={
                    "request_key": digest,
                    "request_path": path,
                    "agent_name": "writer",
                    "temperature": 0.1,
                    "max_tokens": 100,
                    "use_cache": False,
                },
                result={
                    "text": "legacy result",
                    "conversation": history,
                    "input_mode": "text",
                },
            )
        )
        await session.commit()
    agent = fake_agent()
    async with queue.task_context(task):
        assert (
            await queue.submit_completion(agent, "legacy", None, False, {})
            == "legacy result"
        )
        assert agent.conversation_history == [history]
        assert agent._last_input_mode == "text"
        waiting = asyncio.create_task(
            queue.submit_completion(
                fake_agent(max_tokens=200), "legacy", None, False, {}
            )
        )
        try:
            items = await wait_for_items(db, 2)
            assert sorted(item.status for item in items) == ["queued", "succeeded"]

            async def routes():
                return [route()]

            async def complete(item, chosen):
                assert item.payload["max_tokens"] == 200
                return "new settings result"

            dispatcher = queue.AIWorkDispatcher(routes, complete)
            await dispatcher.tick()
            assert await asyncio.wait_for(waiting, 3) == "new settings result"
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
        prompt = queue._request(item.payload["request_path"])["prompt"]
        await gates[prompt].wait()
        return prompt

    dispatcher = queue.AIWorkDispatcher(routes, complete)
    async with queue.task_context(task) as state:
        pending = [
            asyncio.create_task(
                queue.submit_completion(fake_agent(), prompt, None, False, {})
            )
            for prompt in gates
        ]
        try:
            await wait_for_items(db, 2)
            assert await dispatcher.tick() == 2
            gates["first"].set()
            assert await asyncio.wait_for(pending[0], 3) == "first"
            assert state.waiting == 1 and "parent" in queue.waiting_parent_uuids()
            async with db() as session:
                stored = (await session.execute(select(Task))).scalar_one()
                assert stored.input_data["ai_waiting"] is True
            gates["second"].set()
            assert await asyncio.wait_for(pending[1], 3) == "second"
            assert state.waiting == 0 and not queue.waiting_parent_uuids()
        finally:
            for gate in gates.values():
                gate.set()
            for waiting in pending:
                waiting.cancel()
            await asyncio.gather(
                *pending, *dispatcher.active.values(), return_exceptions=True
            )


async def test_parent_cancellation_persists_child_terminal_status(db):
    task = fake_parent()
    task.metadata_ = {"cancel_requested": True}
    async with db() as session:
        session.add(task)
        await session.commit()
    from src.services.exceptions import TaskCancelledError

    async with queue.task_context(task):
        with pytest.raises(TaskCancelledError):
            await queue.submit_completion(fake_agent(), "cancel", None, False, {})
    async with db() as session:
        child = (await session.execute(select(AIWorkItem))).scalar_one()
        assert child.status == "cancelled" and child.finished_at is not None
        parent = (await session.execute(select(Task))).scalar_one()
        assert parent.input_data["ai_waiting"] is False
    assert not queue.waiting_parent_uuids()


@pytest.mark.parametrize("next_type", [TaskType.CRAWL_DATA, TaskType.GENERATE_SECTION])
async def test_real_worker_releases_waiting_parent_concurrency_slot(
    db, monkeypatch, next_type
):
    """A waiting AI parent frees its own lane, so another parent can start."""
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
            assert "parent" in excluded_task_uuids
            assert "parent" in queue.waiting_parent_uuids()
            claimed.append("next")
            return "next", next_type
        return None

    async def execute(task_uuid):
        if task_uuid == "parent":
            async with queue.task_context(parent):
                assert (
                    await queue.submit_completion(
                        fake_agent(), "worker", None, False, {}
                    )
                    == "done"
                )
        else:
            assert "parent" in queue.waiting_parent_uuids()
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

            dispatcher = queue.AIWorkDispatcher(routes, complete)
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
    monkeypatch.setattr(worker, "GENERAL_WORKER_CONCURRENCY", 0)
    monkeypatch.setattr(worker, "RELEASE_WORKER_CONCURRENCY", 0)
    monkeypatch.setattr(worker, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker, "AIWorkDispatcher", Dispatcher)
    monkeypatch.setattr(
        worker,
        "_load_runtime_routes",
        AsyncMock(return_value=[{"available_for_routing": True, "runtime_model_capacity": 1}]),
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
    assert not queue.waiting_parent_uuids()


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
    async with queue.task_context(task) as state:
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
    async with queue.task_context(task):
        waiting = asyncio.create_task(
            queue.submit_completion(fake_agent(), "autopilot prompt", None, False, {})
        )
        try:
            item = (await wait_for_items(db, 1))[0]
            assert item.priority == "low"
            report_id = await add(
                line="report_section", priority="high", parent="report-parent"
            )
            assert (await queue.claim_work(route())).id == report_id
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
        item = await queue.claim_work(route())
    finally:
        event.remove(Session, "loaded_as_persistent", loaded_item)
    assert item is not None
    assert loaded == [item.id]


@pytest.mark.parametrize("eligible_priority", ["urgent", "high"])
async def test_claim_pages_past_avoided_head_without_starving_eligible_work(
    db, eligible_priority
):
    created = datetime(2026, 10, 30, tzinfo=timezone.utc)
    blocked_count = queue.CLAIM_SCAN_PAGE_SIZE * 2 + 3
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
    item = await queue.claim_work(
        route("provider:first"), alternatives={"provider:first", "provider:second"}
    )
    assert item.id == expected_id
    other = await queue.claim_work(
        route("provider:second"), alternatives={"provider:first", "provider:second"}
    )
    assert other.id != expected_id
    assert other.priority == "urgent"


async def test_claim_all_avoided_work_stays_available_for_other_route(db):
    work_id = await queue.enqueue_work(
        parent_task_uuid="parent",
        line="report_review",
        priority="high",
        payload={"avoid_models": ["first"]},
    )
    assert await queue.claim_work(route(), alternatives={"first", "second"}) is None
    assert (await row(db, work_id)).status == "queued"
    assert (
        await queue.claim_work(route("second"), alternatives={"first", "second"})
    ).id == work_id


async def test_same_parent_waiters_share_cancellation_read():
    from unittest.mock import AsyncMock

    state = queue.ParentContext("parent", "knowledge_brief", "low")
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(first=lambda: (TaskStatus.RUNNING, {}))
        )
    )
    assert (
        await asyncio.gather(
            *(queue._parent_cancel_requested(db, state) for _ in range(20))
        )
        == [False] * 20
    )
    db.execute.assert_awaited_once()


async def test_terminal_result_rechecks_cancellation_despite_recent_cached_allow(db):
    from src.services.exceptions import TaskCancelledError

    task = fake_parent()
    path, digest = queue._write_request(
        task.task_uuid, {"prompt": "already completed", "system": None, "kwargs": {}}
    )
    async with db() as session:
        session.add(task)
        session.add(
            AIWorkItem(
                parent_task_uuid="parent",
                line="report_section",
                priority="high",
                status="succeeded",
                payload={
                    "request_key": digest,
                    "request_path": path,
                    "agent_name": "writer",
                    "temperature": 0.1,
                    "max_tokens": 100,
                    "use_cache": False,
                },
                result={"text": "must not be consumed"},
            )
        )
        await session.commit()
    async with queue.task_context(task) as state:
        async with db() as session:
            assert not await queue._parent_cancel_requested(session, state)
            task_row = (await session.execute(select(Task))).scalar_one()
            task_row.status = TaskStatus.CANCELLED
            await session.commit()
        with pytest.raises(TaskCancelledError):
            await queue.submit_completion(
                fake_agent(), "already completed", None, False, {}
            )
