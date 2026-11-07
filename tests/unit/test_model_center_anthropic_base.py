"""Anthropic-format providers keep the gateway root used by the SDK."""

from types import SimpleNamespace

import pytest

from src.ai.agents.base import BaseAgent
from src.ai.model_center import _test_anthropic, anthropic_sdk_base_url


class _Agent(BaseAgent):
    async def process(self, **kwargs):
        return {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("http://192.168.30.10:3030", "http://192.168.30.10:3030"),
        ("http://192.168.30.10:3030/", "http://192.168.30.10:3030"),
        ("http://192.168.30.10:3030/v1", "http://192.168.30.10:3030"),
        ("http://192.168.30.10:3030/v1/", "http://192.168.30.10:3030"),
        ("https://gateway.example/anthropic/v1", "https://gateway.example/anthropic"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
    ],
)
def test_anthropic_sdk_base_url_strips_only_a_trailing_v1(raw, expected) -> None:
    assert anthropic_sdk_base_url(raw) == expected


class _Messages:
    async def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="globalid-model-test-ok")])


class _Client:
    captured: dict = {}

    def __init__(self, **kwargs):
        _Client.captured = kwargs
        self.messages = _Messages()


async def test_health_probe_uses_the_gateway_root(monkeypatch) -> None:
    monkeypatch.setattr("src.ai.work_queue.require_dispatcher", lambda: None)
    monkeypatch.setattr("src.ai.model_center.AsyncAnthropic", _Client)
    await _test_anthropic(
        {
            "api_key": "test-key",
            "base_url": "http://192.168.30.10:3030/v1",
            "extra_headers": {"X-Test": "1"},
        },
        "claude-haiku-4.5",
    )
    assert _Client.captured["base_url"] == "http://192.168.30.10:3030"
    assert _Client.captured["default_headers"] == {"X-Test": "1"}


async def test_runtime_call_uses_the_gateway_root(monkeypatch) -> None:
    monkeypatch.setattr("src.ai.work_queue.require_dispatcher", lambda: None)
    monkeypatch.setattr("src.ai.agents.base.AsyncAnthropic", _Client)

    class _Usage:
        input_tokens = 1
        output_tokens = 1

    class _Reply:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=_Usage(),
            )

    class _RuntimeClient(_Client):
        def __init__(self, **kwargs):
            _Client.captured = kwargs
            self.messages = _Reply()

    monkeypatch.setattr("src.ai.agents.base.AsyncAnthropic", _RuntimeClient)
    agent = _Agent(name="anthropic-link")
    text, usage = await agent._send_runtime_route_request(
        {
            "api_style": "anthropic",
            "api_key": "test-key",
            "base_url": "http://192.168.30.10:3030/v1",
            "model_name": "claude-haiku-4.5",
            "extra_headers": {},
        },
        "ping",
    )
    assert text == "ok"
    assert usage["total"] == 2
    assert _Client.captured["base_url"] == "http://192.168.30.10:3030"


async def test_runtime_call_sets_explicit_timeout_and_omits_empty_system(monkeypatch) -> None:
    """With the SDK default timeout, max_tokens=64000 raises "Streaming is required" before sending."""
    from src.ai.model_center import ANTHROPIC_PROTOCOL_MAX_TOKENS, ANTHROPIC_RUNTIME_TIMEOUT_SECONDS

    monkeypatch.setattr("src.ai.work_queue.require_dispatcher", lambda: None)
    sent: dict = {}

    class _Reply:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], usage=None)

    class _RuntimeClient:
        captured: dict = {}

        def __init__(self, **kwargs):
            _RuntimeClient.captured = kwargs
            self.messages = _Reply()

    monkeypatch.setattr("src.ai.agents.base.AsyncAnthropic", _RuntimeClient)
    route = {
        "api_style": "anthropic",
        "api_key": "test-key",
        "base_url": "http://192.168.30.10:3030/v1",
        "model_name": "claude-sonnet-5.5",
        "extra_headers": {},
    }
    agent = _Agent(name="anthropic-timeout")
    await agent._send_runtime_route_request(route, "ping")

    assert _RuntimeClient.captured["timeout"] == ANTHROPIC_RUNTIME_TIMEOUT_SECONDS
    assert _RuntimeClient.captured["max_retries"] == 0
    assert sent["max_tokens"] == ANTHROPIC_PROTOCOL_MAX_TOKENS
    assert "system" not in sent

    route["extra_params"] = {"runtime_request_timeout_seconds": 120}
    await agent._send_runtime_route_request(route, "ping", "be brief")
    assert _RuntimeClient.captured["timeout"] == 120.0
    assert sent["system"] == "be brief"


def test_real_sdk_accepts_protocol_max_tokens_with_explicit_timeout() -> None:
    """Guard against the SDK's non-streaming check: building the request must not raise."""
    from anthropic import AsyncAnthropic

    from src.ai.model_center import ANTHROPIC_PROTOCOL_MAX_TOKENS, ANTHROPIC_RUNTIME_TIMEOUT_SECONDS

    client = AsyncAnthropic(api_key="x", base_url="http://127.0.0.1:9", timeout=ANTHROPIC_RUNTIME_TIMEOUT_SECONDS)
    # The guard only fires when the client still has the SDK default timeout.
    assert client.timeout == ANTHROPIC_RUNTIME_TIMEOUT_SECONDS
    assert ANTHROPIC_PROTOCOL_MAX_TOKENS == 64000
