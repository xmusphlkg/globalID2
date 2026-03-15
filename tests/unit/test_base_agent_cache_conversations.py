import time

import pytest

from src.ai.agents.base import BaseAgent


class DummyCache:
    def __init__(self, cached_value=None):
        self.cached_value = cached_value
        self.last_set_key = None
        self.last_set_value = None
        self.last_set_ttl = None

    async def get(self, key: str):
        return self.cached_value

    async def set(self, key: str, value, ttl=None):
        self.last_set_key = key
        self.last_set_value = value
        self.last_set_ttl = ttl
        return True


class DummyAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="Dummy", model="dummy-model", provider="dummy-provider")
        self.provider_calls = 0

    async def process(self, **kwargs):
        return {}

    async def _complete_with_provider(self, provider: str, prompt: str, system: str | None, **kwargs):
        self.provider_calls += 1
        return "live-response", {"prompt": 4, "completion": 5, "total": 9}


@pytest.mark.asyncio
async def test_cache_hit_records_conversation_history(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)

    agent = DummyAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", True, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    agent.cache = DummyCache(
        {
            "response": "cached-response",
            "token_usage": {"total": 7},
            "model": "cached-model",
            "provider": "cached-provider",
        }
    )

    result = await agent.complete(prompt="hello", system="system")

    assert result == "cached-response"
    assert agent.provider_calls == 0
    assert len(agent.conversation_history) == 1
    assert agent.conversation_history[0]["response"] == "cached-response"
    assert agent.conversation_history[0]["model"] == "cached-model"
    assert agent.conversation_history[0]["provider"] == "cached-provider"
    assert agent.conversation_history[0]["metadata"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_live_completion_caches_structured_payload(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)

    agent = DummyAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", True, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "cache_ttl", 1, raising=False)
    agent.cache = DummyCache()

    result = await agent.complete(prompt="hello", system="system")

    assert result == "live-response"
    assert agent.provider_calls == 1
    assert agent.cache.last_set_value == {
        "response": "live-response",
        "token_usage": {"prompt": 4, "completion": 5, "total": 9},
        "model": "dummy-model",
        "provider": "dummy-provider",
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
    }
    assert len(agent.conversation_history) == 1
    assert agent.conversation_history[0]["tokens"]["total"] == 9