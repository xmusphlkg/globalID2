import asyncio
import time

import pytest

from src.ai.agents.base import BaseAgent


class DummyCache:
    def __init__(self, cached_value=None):
        self.cached_value = cached_value
        self.last_set_key = None
        self.last_set_value = None
        self.last_set_ttl = None
        self.deleted_keys = []

    async def get(self, key: str):
        return self.cached_value

    async def set(self, key: str, value, ttl=None):
        self.last_set_key = key
        self.last_set_value = value
        self.last_set_ttl = ttl
        return True

    async def delete(self, key: str):
        self.deleted_keys.append(key)
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

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        self.provider_calls += 1
        return "live-response", {"prompt": 4, "completion": 5, "total": 9}


class UnavailableFallbackAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="Fallback", model="bad-model", provider="dummy-provider")
        self.provider_calls = 0
        self.call_models = []

    async def process(self, **kwargs):
        return {}

    async def _complete_with_provider(self, provider: str, prompt: str, system: str | None, **kwargs):
        self.provider_calls += 1
        self.call_models.append(self.model)
        if self.model == "bad-model":
            raise Exception(
                "Error code: 404 - {'error': {'message': 'The model `bad-model` does not exist or you do not have access to it.', 'code': 'model_not_found'}}"
            )
        return "fallback-response", {"prompt": 2, "completion": 3, "total": 5}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        return await self._complete_with_provider(
            str(route.get("provider_key") or "runtime"),
            prompt,
            system,
            **kwargs,
        )


class ProviderAuthenticationFallbackAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="ProviderAuthenticationFallback", model="bad-a", provider="bad-provider")
        self.call_models = []

    async def process(self, **kwargs):
        return {}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        model_name = str(route.get("model_name") or self.model)
        self.call_models.append(model_name)
        if model_name in {"bad-a", "bad-b"}:
            raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid token'}}")
        return "healthy-provider-response", {"prompt": 1, "completion": 1, "total": 2}


class QuotaRecoveryAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="QuotaRecovery", model="hunyuan-pro", provider="dummy-provider")
        self.provider_calls = 0

    async def process(self, **kwargs):
        return {}

    async def _complete_with_provider(self, provider: str, prompt: str, system: str | None, **kwargs):
        self.provider_calls += 1
        if self.provider_calls < 3:
            raise Exception(
                "Error code: 400 - {'error': {'message': '请求限频，请稍后重试', 'code': '2003'}}"
            )
        return "quota-recovered", {"prompt": 1, "completion": 1, "total": 2}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        return await self._complete_with_provider(
            str(route.get("provider_key") or "runtime"),
            prompt,
            system,
            **kwargs,
        )


class ProbeFallbackAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="ProbeFallback", model="qwen-flash-character", provider="dummy-provider")
        self.call_models = []

    async def process(self, **kwargs):
        return {}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        model_name = str(route.get("model_name") or self.model)
        self.call_models.append(model_name)
        if model_name == "qwen-flash-character":
            raise Exception("Error code: 400 - {'error': {'message': '请求限频，请稍后重试', 'code': '2003'}}")
        return "glm-ok", {"prompt": 1, "completion": 1, "total": 2}


class TimeoutFallbackAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="TimeoutFallback", model="slow-model", provider="dummy-provider")
        self.call_models = []

    async def process(self, **kwargs):
        return {}

    async def _complete_with_provider(self, provider: str, prompt: str, system: str | None, **kwargs):
        self.call_models.append(self.model)
        if self.model == "slow-model":
            await asyncio.Event().wait()
        return "fast-response", {"prompt": 1, "completion": 1, "total": 2}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        request_timeout_seconds = kwargs.pop("request_timeout_seconds", None)
        request = self._complete_with_provider(
            str(route.get("provider_key") or "runtime"),
            prompt,
            system,
            **kwargs,
        )
        if request_timeout_seconds is not None:
            return await asyncio.wait_for(request, timeout=request_timeout_seconds)
        return await request


class AdmissionBoundaryAgent(BaseAgent):
    async def process(self, **kwargs):
        return {}

    async def _complete_with_admitted_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        return "admitted-response", {"prompt": 1, "completion": 1, "total": 2}


class EmptyResponseFallbackAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="EmptyResponseFallback", model="empty-model", provider="dummy-provider")
        self.call_models = []

    async def process(self, **kwargs):
        return {}

    async def _complete_with_runtime_route(self, route, prompt: str, system: str | None = None, **kwargs):
        model_name = str(route.get("model_name") or self.model)
        self.call_models.append(model_name)
        if model_name == "empty-model":
            return "", {"prompt": 1, "completion": 0, "total": 1}
        return "fallback-response", {"prompt": 1, "completion": 1, "total": 2}


def runtime_route(
    model_name: str,
    *,
    available: bool = True,
    status: str = "available",
    provider_key: str = "runtime",
    provider_id: int = 999999,
) -> dict[str, object]:
    return {
        "model_key": f"{provider_key}:{model_name}",
        "model_name": model_name,
        "provider_key": provider_key,
        "provider_name": provider_key,
        "model_id": 999999,
        "provider_id": provider_id,
        "available_for_routing": available,
        "last_check_status": status,
        "has_api_key": True,
    }


@pytest.fixture(autouse=True)
def _stub_persisted_runtime_health(monkeypatch):
    """Keep agent unit tests focused on routing decisions, not the database."""
    async def _success(*_args, **_kwargs):
        return {"recorded": True}

    async def _failure(*_args, **_kwargs):
        return {"recorded": True}

    monkeypatch.setattr("src.ai.agents.base.record_route_runtime_success", _success)
    monkeypatch.setattr("src.ai.agents.base.record_route_runtime_failure", _failure)


def test_runtime_candidates_do_not_include_configured_model_failover(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", ["configured-model"], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)
    agent = DummyAgent()

    candidates, chain = agent._build_candidates(
        [
                runtime_route("available-model")
            ]
        )

    assert [candidate["model_name"] for candidate in candidates] == ["available-model"]
    assert all(candidate["route"] is not None for candidate in candidates)
    assert chain == ["available-model"]


def test_preferred_direct_fallback_does_not_jump_ahead_of_healthy_runtime_route() -> None:
    runtime_route = {
        "route_key": "runtime:healthy-model",
        "model_name": "healthy-model",
        "route": {"model_name": "healthy-model"},
    }
    direct_fallback = {
        "route_key": "configured-model",
        "model_name": "configured-model",
        "route": None,
    }

    ordered = BaseAgent._prioritize_candidates(
        [runtime_route, direct_fallback],
        ["configured-model"],
    )

    assert ordered == [runtime_route, direct_fallback]


def test_preferred_runtime_route_key_selects_matching_shard() -> None:
    slow_shard = {
        "route_key": "provider-a:model-shared",
        "model_name": "model-shared",
        "route": {"model_name": "model-shared"},
    }
    preferred_shard = {
        "route_key": "provider-b:model-shared",
        "model_name": "model-shared",
        "route": {"model_name": "model-shared"},
    }

    ordered = BaseAgent._prioritize_candidates(
        [slow_shard, preferred_shard],
        ["provider-b:model-shared"],
    )

    assert ordered == [preferred_shard, slow_shard]


def test_empty_candidate_wait_uses_model_center_runtime_cooldown() -> None:
    wait_seconds = BaseAgent._estimate_wait_seconds_for_empty_candidates(
        runtime_routes=[
            {
                "model_key": "runtime:cooling",
                "model_name": "cooling",
                "runtime_failure_remaining_seconds": 47,
            }
        ],
        chain=[],
        wait_cap_seconds=90,
    )

    assert wait_seconds == 47


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
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [runtime_route("dummy-model")], raising=False)
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
        "provider": "runtime",
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
    }
    assert len(agent.conversation_history) == 1
    assert agent.conversation_history[0]["tokens"]["total"] == 9
    assert agent.conversation_history[0]["metadata"]["runtime_route"]["model_name"] == "dummy-model"


@pytest.mark.asyncio
async def test_completion_cache_can_be_invalidated_after_post_generation_rejection(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    agent = DummyAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", True, raising=False)
    agent.cache = DummyCache()

    deleted = await agent.invalidate_completion_cache(prompt="hello", system="system")

    assert deleted is True
    assert agent.cache.deleted_keys == [agent._make_cache_key("hello", "system")]


@pytest.mark.asyncio
async def test_empty_candidates_wait_then_probe(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    async def _empty_routes():
        return []

    waited = []

    async def _fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr("src.ai.agents.base.get_active_model_routes", _empty_routes)
    monkeypatch.setattr("src.ai.agents.base.get_runtime_routes", _empty_routes)
    monkeypatch.setattr("src.ai.agents.base.asyncio.sleep", _fake_sleep)

    agent = DummyAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_cooldown_seconds", 30, raising=False)

    with pytest.raises(Exception, match="Agent completion failed"):
        await agent.complete(prompt="hello", system="system")

    assert agent.provider_calls == 0
    assert waited
    assert waited[0] >= 1


@pytest.mark.asyncio
async def test_empty_candidates_can_fail_without_waiting_for_recovery(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", ["dummy-model"], raising=False)
    monkeypatch.setattr(
        BaseAgent,
        "MODEL_COOLDOWNS",
        {"dummy-model": time.time() + 30},
        raising=False,
    )
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    async def _unexpected_sleep(_seconds):
        raise AssertionError("recovery wait must be skipped")

    monkeypatch.setattr("src.ai.agents.base.asyncio.sleep", _unexpected_sleep)

    agent = DummyAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)

    with pytest.raises(Exception, match="Agent completion failed"):
        await agent.complete(
            prompt="hello",
            system="system",
            max_quota_recovery_rounds=0,
            wait_for_model_recovery=False,
        )

    assert agent.provider_calls == 0


@pytest.mark.asyncio
async def test_model_not_found_fast_falls_through_to_next_model(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(
        BaseAgent,
        "AVAILABLE_MODEL_ROUTES",
        [runtime_route("bad-model"), runtime_route("good-model")],
        raising=False,
    )
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    persisted = []

    async def _fake_mark_route_unavailable(route, message: str):
        persisted.append((route["model_name"], message))

    monkeypatch.setattr(
        "src.ai.agents.base.mark_route_unavailable",
        _fake_mark_route_unavailable,
    )

    agent = UnavailableFallbackAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "fallback_model", "good-model", raising=False)

    result = await agent.complete(prompt="hello", system="system")

    assert result == "fallback-response"
    assert agent.provider_calls == 2
    assert agent.call_models == ["bad-model", "good-model"]
    assert persisted
    assert persisted[0][0] == "bad-model"


@pytest.mark.asyncio
async def test_provider_auth_failure_skips_siblings_without_retry_and_uses_other_provider(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(
        BaseAgent,
        "AVAILABLE_MODEL_ROUTES",
        [
            runtime_route("bad-a", provider_key="rejected", provider_id=10),
            runtime_route("bad-b", provider_key="rejected", provider_id=10),
            runtime_route("healthy-model", provider_key="healthy", provider_id=20),
        ],
        raising=False,
    )
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    persisted = []

    async def _fake_mark_provider_authentication_failed(route, error=None):
        persisted.append((route["provider_id"], str(error)))
        return {"recorded": True}

    async def _unexpected_sleep(_seconds):
        raise AssertionError("credential failures must not retry or back off")

    async def _unexpected_runtime_failure_record(*_args, **_kwargs):
        raise AssertionError("credential failures must not be recorded as transport failures")

    monkeypatch.setattr(
        "src.ai.agents.base.mark_provider_authentication_failed",
        _fake_mark_provider_authentication_failed,
    )
    monkeypatch.setattr(
        "src.ai.agents.base.record_route_runtime_failure",
        _unexpected_runtime_failure_record,
    )
    monkeypatch.setattr("src.ai.agents.base.asyncio.sleep", _unexpected_sleep)

    agent = ProviderAuthenticationFallbackAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)

    result = await agent.complete(
        prompt="hello",
        system="system",
        max_attempts_per_model=3,
        max_quota_recovery_rounds=0,
        wait_for_model_recovery=False,
    )

    assert result == "healthy-provider-response"
    assert agent.call_models == ["bad-a", "healthy-model"]
    assert persisted == [(10, "Error code: 401 - {'error': {'message': 'Invalid token'}}")]


@pytest.mark.asyncio
async def test_per_route_timeout_falls_through_to_next_model(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(
        BaseAgent,
        "AVAILABLE_MODEL_ROUTES",
        [runtime_route("slow-model"), runtime_route("fast-model")],
        raising=False,
    )
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    agent = TimeoutFallbackAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)

    result = await agent.complete(
        prompt="hello",
        system="system",
        model_request_timeout_seconds=0.01,
        timeout_cooldown_seconds=30,
    )

    assert result == "fast-response"
    assert agent.call_models == ["slow-model", "fast-model"]
    assert BaseAgent._is_model_cooling_down("slow-model") is True


@pytest.mark.asyncio
async def test_runtime_request_timeout_starts_after_model_center_admission(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    agent = AdmissionBoundaryAgent(name="AdmissionBoundary", model="dummy", provider="dummy")

    class Lease:
        async def release(self, *, success: bool):
            assert success is True

    async def delayed_admission(_route):
        await asyncio.sleep(0.03)
        return Lease()

    monkeypatch.setattr("src.ai.agents.base.acquire_runtime_route_admission", delayed_admission)

    result = await agent._complete_with_runtime_route(
        runtime_route("dummy"),
        "hello",
        request_timeout_seconds=0.01,
    )

    assert result[0] == "admitted-response"
    assert 0 <= agent._runtime_route_request_duration_seconds < 0.01


@pytest.mark.asyncio
async def test_empty_runtime_completion_falls_through_to_next_model(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    routes = [runtime_route("empty-model"), runtime_route("fallback-model")]
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", routes, raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    agent = EmptyResponseFallbackAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)

    result = await agent.complete(
        prompt="hello",
        system="system",
        max_attempts_per_model=1,
        max_quota_recovery_rounds=0,
        wait_for_model_recovery=False,
    )

    assert result == "fallback-response"
    assert agent.call_models == ["empty-model", "fallback-model"]


@pytest.mark.asyncio
async def test_per_model_attempt_limit_avoids_blind_transient_retries(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [runtime_route("dummy-model")], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", ["dummy-model"], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    calls = []

    async def fail_once(route, prompt, system, **kwargs):
        calls.append((route, prompt, system, kwargs))
        raise RuntimeError("temporary upstream failure")

    agent = DummyAgent()
    monkeypatch.setattr(agent, "_complete_with_runtime_route", fail_once)
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)

    with pytest.raises(Exception, match="Agent completion failed"):
        await agent.complete(
            prompt="hello",
            system="system",
            max_attempts_per_model=1,
            max_quota_recovery_rounds=0,
            wait_for_model_recovery=False,
        )

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_quota_recovery_waits_long_and_retries_multiple_rounds(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    route = runtime_route("hunyuan-pro")
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES", [route], raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_ROUTES_LOADED_AT", time.time(), raising=False)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    waited = []

    async def _fake_sleep(seconds):
        waited.append(int(seconds))

    async def _runtime_routes():
        return [route]

    monkeypatch.setattr("src.ai.agents.base.get_active_model_routes", _runtime_routes)
    monkeypatch.setattr("src.ai.agents.base.get_runtime_routes", _runtime_routes)
    monkeypatch.setattr("src.ai.agents.base.asyncio.sleep", _fake_sleep)

    agent = QuotaRecoveryAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "fallback_model", "", raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_cooldown_seconds", 300, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_wait_cap_seconds", 900, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_recovery_max_rounds", 4, raising=False)

    result = await agent.complete(prompt="hello", system="system")

    assert result == "quota-recovered"
    assert agent.provider_calls == 3
    assert waited[0] == 300
    assert waited[1] in {299, 300}


@pytest.mark.asyncio
async def test_probe_candidates_never_bypass_durable_rate_limit_cooldown(monkeypatch):
    monkeypatch.setattr(BaseAgent, "_init_clients", lambda self: None)
    monkeypatch.setattr(BaseAgent, "AVAILABLE_MODEL_CHAIN", [], raising=False)
    monkeypatch.setattr(BaseAgent, "MODEL_COOLDOWNS", {}, raising=False)
    monkeypatch.setattr(BaseAgent, "ROUTE_COOLDOWNS", {}, raising=False)

    runtime_routes = [
        {
            "model_key": "qianwen-default:qwen-flash-character",
            "model_name": "qwen-flash-character",
            "provider_key": "qianwen-default",
            "provider_name": "qianwen",
            "available_for_routing": False,
            "last_check_status": "rate_limited",
            "rate_limit_remaining_seconds": 120,
        },
        {
            "model_key": "qianwen-default:glm-5",
            "model_name": "glm-5",
            "provider_key": "qianwen-default",
            "provider_name": "qianwen",
            "available_for_routing": False,
            "last_check_status": "available",
            "rate_limit_remaining_seconds": 120,
        },
    ]

    async def _empty_active_routes():
        return []

    async def _all_runtime_routes():
        return runtime_routes

    waited = []

    async def _fake_sleep(seconds):
        waited.append(int(seconds))

    monkeypatch.setattr("src.ai.agents.base.get_active_model_routes", _empty_active_routes)
    monkeypatch.setattr("src.ai.agents.base.get_runtime_routes", _all_runtime_routes)
    monkeypatch.setattr("src.ai.agents.base.asyncio.sleep", _fake_sleep)

    agent = ProbeFallbackAgent()
    monkeypatch.setattr(agent.config.ai, "enable_cache", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "enable_rate_limiting", False, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_cooldown_seconds", 120, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_wait_cap_seconds", 120, raising=False)
    monkeypatch.setattr(agent.config.ai, "rate_limit_recovery_max_rounds", 0, raising=False)

    with pytest.raises(Exception, match="Agent completion failed"):
        await agent.complete(prompt="hello", system="system")

    assert agent.call_models == []
    assert waited
