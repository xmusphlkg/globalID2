"""Success projections are bounded, coalesced and safe during concurrent marks."""

import asyncio
from unittest.mock import AsyncMock

from src.ai.runtime import dispatcher, progress


def success(parent="parent", generation=0):
    return {
        "parent": parent,
        "execution_generation": generation,
        "status": "succeeded",
        "record_error": False,
    }


async def test_completion_burst_coalesces_and_ticks_are_throttled(monkeypatch):
    project = AsyncMock(return_value=True)
    monkeypatch.setattr(progress, "_project_success", project)
    buffer = progress.SuccessProgressBuffer()
    for _ in range(500):
        buffer.mark(success())
    assert len(buffer._pending) == 1
    await buffer.flush()
    project.assert_awaited_once_with("parent", 0)
    assert not buffer._pending
    buffer.mark(success())
    await buffer.flush()
    assert project.await_count == 1
    await buffer.flush(force=True)
    assert project.await_count == 2


async def test_buffer_bound_and_batch_limit_keep_busy_parents_fair(monkeypatch):
    project = AsyncMock(return_value=False)
    monkeypatch.setattr(progress, "_project_success", project)
    buffer = progress.SuccessProgressBuffer()
    buffer.MAX_PENDING = 20
    for i in range(50):
        buffer.mark(success(str(i)))
    assert len(buffer._pending) == 20
    await buffer.flush()
    assert project.await_count == 16
    assert len(buffer._pending) == 20
    first_round = [call.args[0] for call in project.await_args_list]
    assert first_round == [str(i) for i in range(30, 46)]
    await buffer.flush(force=True)
    assert [call.args[0] for call in project.await_args_list[16:20]] == [
        "46",
        "47",
        "48",
        "49",
    ]


async def test_completion_during_projection_survives_previous_flush(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def project(parent, generation):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(progress, "_project_success", project)
    buffer = progress.SuccessProgressBuffer()
    buffer.mark(success())
    flush = asyncio.create_task(buffer.flush())
    await entered.wait()
    buffer.mark(success())
    release.set()
    await flush
    assert ("parent", 0) in buffer._pending
    await buffer.flush(force=True)
    assert not buffer._pending


async def test_shutdown_closes_transport_before_bounded_projection(monkeypatch):
    order = []

    async def release(owner):
        order.append("release")

    async def close():
        order.append("close")

    async def project(**kwargs):
        order.append("project")
        await asyncio.Event().wait()

    monkeypatch.setattr(dispatcher, "release_owned_leases", release)
    worker = dispatcher.AIWorkDispatcher(route_loader=AsyncMock(return_value=[]))
    monkeypatch.setattr(worker.transport, "close", close)
    monkeypatch.setattr(worker.progress, "flush", project)
    stop = asyncio.Event()
    stop.set()
    async with asyncio.timeout(2):
        await worker.run(stop)
    assert order == ["release", "close", "project"]
