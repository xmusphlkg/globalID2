"""Cancelling a parent stops queued model work and releases stream children."""

import asyncio
from contextlib import asynccontextmanager

import pytest

import src.core as core
from src.services import ai_content_governance_service as module


@pytest.mark.parametrize("items", [1, 100])
async def test_cancel_stream_during_drain_or_full_feeder_stops_children(monkeypatch, items):
    service = module.AIContentGovernanceService()
    started = asyncio.Event()
    stopped = asyncio.Event()
    claimed = False
    calls = 0

    @asynccontextmanager
    async def database():
        yield object()

    async def claim(*_args):
        nonlocal claimed
        if claimed:
            return 0, [], 0
        claimed = True
        return items, [(i, None, i) for i in range(items)], 0

    async def decide(*_args):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class Pool:
        def completer(self, avoid=()):
            return object()

    async def route_pool(_self):
        return Pool()

    monkeypatch.setattr(core, "get_db", database)
    monkeypatch.setattr(module.ReviewClaims, "route_pool", route_pool)
    monkeypatch.setattr(service, "_claim_stream_items", claim)
    monkeypatch.setattr(service, "_decide_article", decide)
    before = set(asyncio.all_tasks())
    task = asyncio.create_task(service.stream_review("articles", workers=1, fetch_size=100))
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.sleep(0)
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=1)
    assert task in done, "Cancellation must not wait for queued model calls"
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert calls == 1
    assert not (set(asyncio.all_tasks()) - before)
