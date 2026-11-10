"""Notifications scale with changed IDs; reconciliation scales with batches."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.ai.runtime.signals import ActivitySignals, BATCH_SIZE


async def test_notifications_do_not_wake_unrelated_waiters():
    hub = ActivitySignals(None)
    events = [asyncio.Event() for _ in range(600)]
    for index, event in enumerate(events):
        hub._events[index].add(event)
    hub.notify([17])
    assert [i for i, event in enumerate(events) if event.is_set()] == [17]


async def test_reconciliation_batches_waiters_without_reading_result_payloads():
    queries = []

    @asynccontextmanager
    async def database():
        class Session:
            async def execute(self, query):
                queries.append(query)
                return SimpleNamespace(all=lambda: [])

        yield Session()

    hub = ActivitySignals(database)
    for index in range(BATCH_SIZE * 2 + 1):
        hub._events[index].add(asyncio.Event())
    await hub._scan()
    assert len(queries) == 3
    for query in queries:
        assert "ai_work_items.result" not in str(query)
        assert "ai_work_items.payload" not in str(query)
    # Missing rows are terminal failures for their own waiters, not silent hangs.
    assert all(next(iter(events)).is_set() for events in hub._events.values())


async def test_listener_retries_initial_database_failure(monkeypatch):
    import src.ai.runtime.signals as module

    calls = 0
    retried = asyncio.Event()

    @asynccontextmanager
    async def database():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary pool outage")
        retried.set()
        yield SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        )

    original_sleep = asyncio.sleep

    async def immediate(_seconds):
        await original_sleep(0)

    monkeypatch.setattr(module.asyncio, "sleep", immediate)
    await asyncio.wait_for(ActivitySignals(database)._listen(), 1)
    assert calls == 2 and retried.is_set()


async def test_last_subscriber_cleans_up_listener_and_reconciler():
    @asynccontextmanager
    async def database():
        yield SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        )

    hub = ActivitySignals(database)
    async with hub.subscribe([42], "parent"):
        tasks = list(hub._tasks)
        await asyncio.sleep(0)
        assert tasks
    assert all(task.done() for task in tasks)
    assert not hub._tasks and not hub._parents and not hub._events


def test_governance_parent_aggregates_deferred_items_without_failing_the_run():
    from src.ai.runtime.context import (
        ParentContext,
        _PARENT,
        assert_children_succeeded,
        parent_line,
    )

    for operation in ("content_governance", "governance_stream", "governance_simple"):
        task = SimpleNamespace(
            input_data={"operation": operation}, task_type="agent_workflow"
        )
        line = parent_line(task)
        assert line == "content_governance"
        parent = ParentContext("parent", line, "normal", failures=["one item deferred"])
        token = _PARENT.set(parent)
        try:
            assert_children_succeeded()
        finally:
            _PARENT.reset(token)


async def test_missing_parent_wakes_business_waiter_but_not_runtime_probe():
    @asynccontextmanager
    async def database():
        yield SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    all=lambda: [
                        (1, "queued", "deleted-parent", None, None),
                        (2, "queued", "runtime", None, None),
                    ]
                )
            )
        )

    hub = ActivitySignals(database)
    business, probe = asyncio.Event(), asyncio.Event()
    hub._events[1].add(business)
    hub._events[2].add(probe)
    await hub._scan()
    assert business.is_set() and not probe.is_set()


def test_terminal_parent_cannot_start_or_consume_more_model_work():
    from src.ai.runtime.contracts import parent_cancelled
    from src.domain import TaskStatus

    for status in (TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.COMPLETED):
        assert parent_cancelled(
            SimpleNamespace(status=status, metadata_={}), parent_task_uuid="parent"
        )
    for status in (TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.RETRYING):
        assert not parent_cancelled(
            SimpleNamespace(status=status, metadata_={}), parent_task_uuid="parent"
        )
