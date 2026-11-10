"""Offline SDK boundary tests: reuse, deadlines, bounded protocol negotiation."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.ai import providers
from src.ai.attachments import PROBE_KEY
from src.ai.providers import ProviderTransport, extract_openai_response
from src.ai.runtime.context import _DISPATCHING

ROUTE = {
    "model_name": "model",
    "model_key": "p:model",
    "api_key": "secret",
    "base_url": "https://gateway.invalid/v1",
}


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


@pytest.fixture
def dispatcher():
    token = _DISPATCHING.set(True)
    try:
        yield
    finally:
        _DISPATCHING.reset(token)


@pytest.fixture
def clients(monkeypatch):
    constructed = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.create = AsyncMock(
                return_value={"choices": [{"message": {"content": "answer"}}]}
            )
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.close = AsyncMock()
            constructed.append(self)

    monkeypatch.setattr(providers, "AsyncOpenAI", Client)
    return constructed


async def test_provider_connections_are_shared_across_models_and_closed_once(
    dispatcher, clients
):
    transport = ProviderTransport()
    await transport.send(ROUTE, "one")
    await transport.send({**ROUTE, "model_name": "other"}, "two")
    assert len(clients) == 1
    assert clients[0].kwargs["max_retries"] == 0
    assert clients[0].create.await_count == 2
    await transport.send({**ROUTE, "api_key": "rotated"}, "three")
    assert len(clients) == 2
    await transport.close()
    await transport.close()
    for client in clients:
        client.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match="closed"):
        await transport.send(ROUTE, "four")


@pytest.mark.parametrize("style", ["openai_compatible", "anthropic"])
async def test_real_sdk_does_not_receive_internal_native_file_probe_state(
    dispatcher, monkeypatch, style
):
    """Real SDK signatures reject leaked metadata before any HTTP request exists."""
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    bodies = []

    async def respond(request):
        bodies.append(json.loads(request.content))
        if style == "anthropic":
            response = {
                "id": "msg_offline",
                "type": "message",
                "role": "assistant",
                "model": "model",
                "content": [{"type": "text", "text": "answer"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
        else:
            response = {
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            }
        return httpx.Response(200, json=response)

    sdk = AsyncAnthropic if style == "anthropic" else AsyncOpenAI

    def factory(**kwargs):
        return sdk(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )

    monkeypatch.setattr(
        providers, "AsyncAnthropic" if style == "anthropic" else "AsyncOpenAI", factory
    )
    options = {
        "runtime_max_concurrency": 4,
        "routing_state": {"runtime_success_count": 3},
        PROBE_KEY: {"supported": True, "checked_at_epoch": 12345},
        "extra_body": {"provider_extension": {"mode": "test"}},
    }
    original = copy.deepcopy(options)
    transport = ProviderTransport()
    try:
        answer, tokens = await transport.send(
            {**ROUTE, "api_style": style, "extra_params": options}, "offline prompt"
        )
    finally:
        await transport.close()
    assert answer == "answer" and tokens["total"] == 3
    assert len(bodies) == 1
    assert PROBE_KEY not in bodies[0]
    assert "routing_state" not in bodies[0]
    assert "runtime_max_concurrency" not in bodies[0]
    assert bodies[0]["provider_extension"] == {"mode": "test"}
    assert options == original


async def test_close_attempts_every_client_even_when_one_cleanup_fails(
    dispatcher, clients
):
    transport = ProviderTransport()
    await transport.send(ROUTE, "one")
    await transport.send({**ROUTE, "api_key": "other"}, "two")
    clients[0].close.side_effect = RuntimeError("already closed")
    await transport.close()
    clients[1].close.assert_awaited_once()


async def test_optional_negotiation_is_bounded_and_does_not_mutate_configuration(
    dispatcher,
):
    create = AsyncMock(
        side_effect=[
            HTTPError(400, "unsupported 'thinking'"),
            TypeError("unexpected keyword argument 'response_format'"),
            "ok",
        ]
    )
    payload = {
        "model": "m",
        "messages": [],
        "extra_body": {"thinking": {"type": "enabled"}, "other": 1},
        "response_format": {"type": "json_object"},
    }
    original = copy.deepcopy(payload)
    assert await providers._create_with_optional_negotiation(create, payload) == "ok"
    assert create.await_count == 3
    assert payload == original
    assert create.await_args_list[-1].kwargs["extra_body"] == {"other": 1}
    assert "response_format" not in create.await_args_list[-1].kwargs


@pytest.mark.parametrize(
    "error",
    [
        HTTPError(429, "unsupported 'temperature'"),
        HTTPError(503, "unsupported 'temperature'"),
        HTTPError(400, "unsupported 'model'"),
        TypeError("unexpected keyword argument 'private_retry_option'"),
        ConnectionError("unsupported 'temperature'"),
    ],
)
async def test_core_fields_and_provider_failures_are_not_negotiated(dispatcher, error):
    create = AsyncMock(side_effect=error)
    with pytest.raises(type(error)) as raised:
        await providers._create_with_optional_negotiation(
            create,
            {
                "model": "m",
                "messages": [],
                "temperature": 0.2,
                "private_retry_option": 1,
            },
        )
    assert raised.value is error
    create.assert_awaited_once()


async def test_repeated_optional_rejections_do_not_exceed_three_calls(dispatcher):
    errors = [
        HTTPError(400, "unsupported 'temperature'"),
        HTTPError(400, "unsupported 'top_p'"),
        HTTPError(400, "unsupported 'response_format'"),
    ]
    create = AsyncMock(side_effect=errors)
    with pytest.raises(HTTPError) as raised:
        await providers._create_with_optional_negotiation(
            create,
            {
                "model": "m",
                "messages": [],
                "temperature": 0.2,
                "top_p": 0.8,
                "response_format": {},
            },
        )
    assert raised.value is errors[-1]
    assert create.await_count == 3


async def test_deadline_cancels_provider_io_without_retry(dispatcher, clients):
    transport = ProviderTransport()
    cancelled = asyncio.Event()

    async def pending(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = transport._client(ROUTE, "openai_compatible")
    client.create.side_effect = pending
    with pytest.raises(TimeoutError):
        await transport.complete(ROUTE, "prompt", request_timeout_seconds=0.01)
    assert cancelled.is_set()
    client.create.assert_awaited_once()
    await transport.close()


def test_reasoning_only_reply_is_not_promoted_to_visible_output():
    response = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": None, "reasoning_content": "private thinking"},
            }
        ],
        "usage": {"completion_tokens": 10},
    }
    with pytest.raises(RuntimeError, match="token budget") as raised:
        extract_openai_response(response)
    assert "private thinking" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "thinking", "text": "private"},
                            {"type": "text", "text": "answer"},
                        ]
                    }
                }
            ]
        },
        {"output_text": "<think>private</think>answer"},
    ],
)
def test_visible_answer_normalization_excludes_reasoning(response):
    assert extract_openai_response(response)[0] == "answer"
