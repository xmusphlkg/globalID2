"""An admitted attempt is independent of cache and observability availability."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.agents import base
from src.ai.agents.base import BaseAgent
from src.ai.runtime.context import _DISPATCHING


class Agent(BaseAgent):
    async def process(self, **kwargs):
        return {}


class Cache:
    def __init__(self):
        self.values = {}
        self.deleted = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ttl):
        self.values[key] = value
        return True

    async def delete(self, key):
        self.deleted.append(key)
        return self.values.pop(key, None) is not None


ROUTE = {
    "model_key": "provider:chosen", "model_name": "chosen", "model_id": "m1",
    "provider_key": "provider", "provider_id": "p1", "temperature": None,
    "available_for_routing": True,
}


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(base, "get_cache", Cache)
    monkeypatch.setattr(base, "get_config", lambda: SimpleNamespace(ai=SimpleNamespace(
        model_chain=[], default_model="bootstrap", default_provider="default",
        enable_cache=True, cache_ttl=1, rate_limit_cooldown_seconds=60,
    )))
    for name in ("record_route_runtime_success", "record_route_runtime_failure", "record_model_call_log",
                 "mark_provider_authentication_failed", "mark_route_rate_limited", "mark_route_unavailable",
                 "clear_route_rate_limit"):
        monkeypatch.setattr(base, name, AsyncMock())
    instance = Agent(name="test")
    instance.transport = SimpleNamespace(complete=AsyncMock(return_value=("answer", {"total": 9}, None)))
    token = _DISPATCHING.set(True)
    try:
        yield instance
    finally:
        _DISPATCHING.reset(token)


async def test_live_completion_and_cache_hit_record_selected_model(agent):
    for _ in range(2):
        assert await agent.complete("question", "system", _scheduled_route=ROUTE) == "answer"
    agent.transport.complete.assert_awaited_once()
    live, cached = agent.get_conversation_history()
    assert live["model"] == cached["model"] == "chosen"
    assert live["provider"] == cached["provider"] == "provider"
    assert live["metadata"]["cache_hit"] is False
    assert cached["metadata"]["cache_hit"] is True
    assert cached["tokens"] == {"total": 9}
    assert cached["system_prompt"] == "system"
    assert cached["metadata"]["runtime_route"] == live["metadata"]["runtime_route"]
    base.record_model_call_log.assert_awaited_once()


async def test_invalidation_uses_replayed_route_cache_key_and_system_prompt(agent):
    await agent.complete("question", "system", _scheduled_route=ROUTE)
    await agent.complete("question", "other system", _scheduled_route=ROUTE)
    await agent.complete("question", "system", _scheduled_route=ROUTE)
    key = agent.get_latest_conversation()["metadata"]["cache_key"]
    assert await agent.invalidate_completion_cache(prompt="question", system="system") is True
    assert agent.cache.deleted == [key]
    assert len(agent.cache.values) == 1
    await agent.complete("question", "system", _scheduled_route=ROUTE)
    assert agent.transport.complete.await_count == 3


async def test_cache_is_partitioned_by_actual_route_and_generation(agent):
    await agent.complete("question", _scheduled_route=ROUTE)
    await agent.complete("question", _scheduled_route={**ROUTE, "model_key": "provider:other", "model_name": "other"})
    agent.temperature = .1
    await agent.complete("question", _scheduled_route=ROUTE)
    await agent.complete("question", _scheduled_route=ROUTE, response_format={"type": "json_object"})
    assert agent.transport.complete.await_count == 4
    assert len(agent.cache.values) == 4


async def test_cached_provenance_distinguishes_providers_with_the_same_model_name(agent):
    other = {**ROUTE, "provider_id": "p2", "provider_key": "other", "model_key": "other:chosen"}
    for route in (ROUTE, other, ROUTE, other):
        await agent.complete("question", _scheduled_route=route)
        entry = agent.get_latest_conversation()
        assert entry["metadata"]["runtime_route"]["model_key"] == route["model_key"]
        assert entry["provider"] == route["provider_key"]
    assert agent.transport.complete.await_count == 2


async def test_cache_and_telemetry_outage_do_not_repeat_success(agent, monkeypatch):
    agent.cache.get = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    agent.cache.set = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    monkeypatch.setattr(base, "record_model_call_log", AsyncMock(side_effect=RuntimeError("db unavailable")))
    monkeypatch.setattr(base, "record_route_runtime_success", AsyncMock(side_effect=RuntimeError("db unavailable")))
    assert await agent.complete("question", _scheduled_route=ROUTE) == "answer"
    agent.transport.complete.assert_awaited_once()
    assert agent.get_latest_conversation()["response"] == "answer"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionError("network down"),
                                  RuntimeError("429 rate limit reached"), RuntimeError("401 invalid token")])
async def test_failure_identity_is_preserved_and_one_route_is_tried(agent, monkeypatch, error):
    agent.transport.complete.side_effect = error
    monkeypatch.setattr(base, "record_model_call_log", AsyncMock(side_effect=RuntimeError("db unavailable")))
    with pytest.raises(type(error)) as raised:
        await agent.complete("question", _scheduled_route=ROUTE)
    assert raised.value is error
    agent.transport.complete.assert_awaited_once()
    assert not agent.cache.values and not agent.conversation_history


async def test_cancellation_propagates_without_model_health_penalty(agent):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    agent.transport.complete.side_effect = pending
    task = asyncio.create_task(agent.complete("question", _scheduled_route=ROUTE))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    base.record_route_runtime_failure.assert_not_awaited()
    base.record_model_call_log.assert_not_awaited()
    assert not agent.cache.values


async def test_empty_response_is_failed_once_and_never_cached(agent):
    agent.transport.complete.return_value = ("  ", {}, None)
    with pytest.raises(RuntimeError, match="empty completion"):
        await agent.complete("question", _scheduled_route=ROUTE)
    agent.transport.complete.assert_awaited_once()
    assert not agent.cache.values
    assert base.record_model_call_log.await_args.kwargs["status"] == "failed"


async def test_attachments_bypass_text_cache(agent):
    agent.transport.complete.return_value = ("document answer", {}, "native_file")
    for _ in range(2):
        await agent.complete("question", _scheduled_route=ROUTE, attachments=[object()])
    assert agent.transport.complete.await_count == 2
    assert not agent.cache.values
    assert agent._last_input_mode == "native_file"


async def test_dispatcher_cannot_execute_without_selected_route(agent):
    with pytest.raises(RuntimeError, match="pin an admitted route"):
        await agent.complete("question")
    agent.transport.complete.assert_not_awaited()
