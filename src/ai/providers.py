"""Dispatcher-owned provider I/O and reusable SDK connections.

The dispatcher selects and leases the route. This module sends only to that
route, with SDK retries disabled. Protocol negotiation may remove a rejected
optional parameter, or replace an unsupported file with its extracted text;
neither path retries network, quota, authentication, or server failures.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from src.ai.attachments import (
    NATIVE_INPUT,
    PROBE_KEY,
    TEXT_INPUT,
    as_native_content,
    as_text,
    looks_like_file_rejection,
    note_native_rejection,
    route_accepts_native_files,
)
from src.ai.catalogue.options import (
    ANTHROPIC_PROTOCOL_MAX_TOKENS,
    ANTHROPIC_RUNTIME_TIMEOUT_SECONDS,
    anthropic_sdk_base_url,
    drop_rejected_optional_parameter,
    runtime_route_request_timeout_seconds,
)
from src.ai.catalogue.responses import visible_assistant_text
from src.core import get_logger

logger = get_logger(__name__)

_INTERNAL_FIELDS = {
    "cooldown_until",
    "last_rate_limit_at",
    "last_recovered_at",
    PROBE_KEY,
}
_INTERNAL_PREFIXES = (
    "routing_",
    "rate_limit_",
    "runtime_",
    "health_check_",
    "availability_",
    "last_runtime_",
)
_OPTIONAL_FIELDS = frozenset(
    {
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "response_format",
        "logprobs",
        "top_logprobs",
        "thinking",
    }
)
_MAX_PARAMETER_ADJUSTMENTS = 2


def sanitize_completion_params(params: dict[str, Any]) -> dict[str, Any]:
    """Exclude catalogue state from the provider's generation parameters."""
    return {
        key: value
        for key, value in (params or {}).items()
        if key not in _INTERNAL_FIELDS and not key.startswith(_INTERNAL_PREFIXES)
    }


async def _create_with_optional_negotiation(create, payload: dict[str, Any]):
    """At most two named optional-field corrections, never retry I/O errors."""
    from src.ai.runtime import require_dispatcher

    require_dispatcher()
    request = copy.deepcopy(payload)
    dropped: set[str] = set()
    for adjustment in range(_MAX_PARAMETER_ADJUSTMENTS + 1):
        try:
            return await create(**request)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if (
                adjustment == _MAX_PARAMETER_ADJUSTMENTS
                or (status is not None and status not in {400, 422})
                or (status is None and not isinstance(exc, TypeError))
            ):
                raise
            candidate = copy.deepcopy(request)
            new_dropped = set(dropped)
            if not drop_rejected_optional_parameter(exc, candidate, new_dropped):
                raise
            if not (new_dropped - dropped) <= _OPTIONAL_FIELDS:
                raise
            request, dropped = candidate, new_dropped
            logger.debug(
                "Provider rejected optional parameters: {}", ", ".join(sorted(dropped))
            )


class ProviderTransport:
    """One dispatcher lifetime; no client construction before its first lease."""

    def __init__(self) -> None:
        self._clients: dict[tuple, Any] = {}
        self._closed = False

    def _client(self, route: dict[str, Any], style: str):
        if self._closed:
            raise RuntimeError("Provider transport is closed")
        base_url = route.get("base_url") or None
        if style == "anthropic":
            base_url = anthropic_sdk_base_url(base_url)
        timeout = runtime_route_request_timeout_seconds(
            route, ANTHROPIC_RUNTIME_TIMEOUT_SECONDS
        )
        headers = dict(route.get("extra_headers") or {})
        key = (
            style,
            base_url,
            route.get("api_key"),
            json.dumps(headers, sort_keys=True),
            timeout,
        )
        if key not in self._clients:
            constructor = AsyncAnthropic if style == "anthropic" else AsyncOpenAI
            self._clients[key] = constructor(
                api_key=route.get("api_key"),
                base_url=base_url,
                default_headers=headers or None,
                timeout=float(timeout),
                max_retries=0,
            )
        return self._clients[key]

    async def close(self) -> None:
        """Close all pooled clients after the dispatcher's active calls finish."""
        self._closed = True
        clients, self._clients = list(self._clients.values()), {}
        results = await asyncio.gather(
            *(client.close() for client in clients), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(
                    "Could not close provider client: {}", type(result).__name__
                )

    async def complete(
        self,
        route: dict[str, Any],
        prompt: str,
        system: str | None = None,
        *,
        temperature: float = 0.7,
        params: dict[str, Any] | None = None,
        attachments=None,
        request_timeout_seconds: float | None = None,
    ) -> tuple[str, dict[str, int], str | None]:
        from src.ai.runtime import require_dispatcher

        require_dispatcher()
        timeout = runtime_route_request_timeout_seconds(route, request_timeout_seconds)
        call = self._complete_input(
            route, prompt, system, temperature, params or {}, list(attachments or [])
        )
        if timeout is None:
            return await call
        return await asyncio.wait_for(call, timeout=max(0.01, float(timeout)))

    async def _complete_input(
        self, route, prompt, system, temperature, params, attachments
    ):
        style = str(route.get("api_style") or "openai_compatible").lower()
        mode = None
        if attachments:
            if any(
                item.native_capable for item in attachments
            ) and route_accepts_native_files(route):
                try:
                    text, usage = await self.send(
                        route,
                        as_native_content(style, prompt, attachments),
                        system,
                        temperature=temperature,
                        params=params,
                    )
                    return text, usage, NATIVE_INPUT
                except Exception as exc:
                    if not looks_like_file_rejection(exc):
                        raise
                    note_native_rejection(route)
                    logger.debug(
                        "Native file rejected by route {}; using extracted text",
                        route.get("model_key"),
                    )
            prompt, mode = as_text(prompt, attachments), TEXT_INPUT
        text, usage = await self.send(
            route, prompt, system, temperature=temperature, params=params
        )
        return text, usage, mode

    async def send(
        self,
        route: dict[str, Any],
        content: Any,
        system: str | None = None,
        *,
        temperature: float = 0.7,
        params: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, int]]:
        from src.ai.runtime import require_dispatcher

        require_dispatcher()
        style = str(route.get("api_style") or "openai_compatible").lower()
        client = self._client(route, style)
        configured = route.get("temperature")
        temperature = temperature if configured is None else float(configured)
        options = sanitize_completion_params(
            {**(route.get("extra_params") or {}), **(params or {})}
        )
        for reserved in (
            "model",
            "messages",
            "system",
            "temperature",
            "max_tokens",
            "max_completion_tokens",
        ):
            options.pop(reserved, None)
        request = {
            **options,
            "model": route["model_name"],
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        if style == "anthropic":
            # Anthropic requires max_tokens. The explicit client timeout permits
            # nonstreaming calls with the protocol ceiling instead of an SDK cap.
            request["max_tokens"] = ANTHROPIC_PROTOCOL_MAX_TOKENS
            if system:
                request["system"] = system
            response = await _create_with_optional_negotiation(
                client.messages.create, request
            )
            text = visible_assistant_text(response.content)
            if not text:
                raise RuntimeError("Model returned an empty completion response")
            usage = getattr(response, "usage", None)
            tokens = (
                {}
                if usage is None
                else {
                    "prompt": usage.input_tokens,
                    "completion": usage.output_tokens,
                    "total": usage.input_tokens + usage.output_tokens,
                }
            )
            return text, tokens
        if system:
            request["messages"].insert(0, {"role": "system", "content": system})
        response = await _create_with_optional_negotiation(
            client.chat.completions.create, request
        )
        return extract_openai_response(response)


def _empty_completion_error(response: Any) -> RuntimeError:
    """Describe an empty upstream response without logging its prompt/body."""
    choices = (
        response.get("choices")
        if isinstance(response, dict)
        else getattr(response, "choices", None)
    )
    first = choices[0] if isinstance(choices, (list, tuple)) and choices else None
    message = (
        first.get("message")
        if isinstance(first, dict)
        else getattr(first, "message", None)
    )
    if message is None and isinstance(first, dict):
        message = first
    if isinstance(message, dict):
        message_fields = sorted(str(key) for key in message.keys())[:12]
        refusal = bool(message.get("refusal"))
        tool_calls = message.get("tool_calls")
        reasoning_content = message.get("reasoning_content")
    else:
        message_fields = sorted(
            key
            for key in ("content", "refusal", "tool_calls", "reasoning_content")
            if getattr(message, key, None) is not None
        )
        refusal = bool(getattr(message, "refusal", None))
        tool_calls = getattr(message, "tool_calls", None)
        reasoning_content = getattr(message, "reasoning_content", None)

    usage = (
        response.get("usage")
        if isinstance(response, dict)
        else getattr(response, "usage", None)
    )
    completion_tokens = (
        usage.get("completion_tokens")
        if isinstance(usage, dict)
        else getattr(usage, "completion_tokens", None)
    )
    finish_reason = (
        first.get("finish_reason")
        if isinstance(first, dict)
        else getattr(first, "finish_reason", None)
    )
    tool_call_count = (
        len(tool_calls)
        if isinstance(tool_calls, (list, tuple))
        else int(bool(tool_calls))
    )
    top_level_fields = (
        sorted(str(key) for key in response.keys())[:12]
        if isinstance(response, dict)
        else []
    )
    explanation = (
        "Model exhausted its completion token budget before producing an answer"
        if finish_reason == "length" and reasoning_content
        else "Model returned an empty completion response"
    )
    return RuntimeError(
        f"{explanation} "
        f"(finish_reason={finish_reason!r}, completion_tokens={completion_tokens!r}, "
        f"reasoning_chars={len(str(reasoning_content or ''))}, "
        f"message_fields={message_fields!r}, refusal_present={refusal}, "
        f"tool_call_count={tool_call_count}, response_type={type(response).__name__}, "
        f"response_fields={top_level_fields!r})"
    )


def _normalize_response_content(content: Any, response: Any) -> str:
    text = visible_assistant_text(content)
    if not text.strip():
        raise _empty_completion_error(response)
    return text


def extract_openai_response(response: Any) -> tuple[str, dict[str, int]]:
    """Normalize OpenAI-compatible responses, including string-returning proxies."""
    token_usage: dict[str, int] = {}
    usage = (
        response.get("usage")
        if isinstance(response, dict)
        else getattr(response, "usage", None)
    )
    if usage:
        prompt_tokens = (
            usage.get("prompt_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "prompt_tokens", None)
        )
        completion_tokens = (
            usage.get("completion_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "completion_tokens", None)
        )
        total_tokens = (
            usage.get("total_tokens")
            if isinstance(usage, dict)
            else getattr(usage, "total_tokens", None)
        )
        token_usage = {
            "prompt": int(prompt_tokens or 0),
            "completion": int(completion_tokens or 0),
            "total": int(
                total_tokens or ((prompt_tokens or 0) + (completion_tokens or 0))
            ),
        }

    if isinstance(response, str):
        return _normalize_response_content(response, response), token_usage

    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return _normalize_response_content(
                        message.get("content"), response
                    ), token_usage
                return _normalize_response_content(
                    first.get("text") or first.get("content"), response
                ), token_usage
        output_text = response.get("output_text")
        if output_text is not None:
            return _normalize_response_content(output_text, response), token_usage
        return _normalize_response_content(
            response.get("content"), response
        ), token_usage

    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return _normalize_response_content(
                getattr(message, "content", None), response
            ), token_usage
        return _normalize_response_content(
            getattr(first, "text", None), response
        ), token_usage

    output_text = getattr(response, "output_text", None)
    if output_text is not None:
        return _normalize_response_content(output_text, response), token_usage
    return _normalize_response_content(
        getattr(response, "content", response), response
    ), token_usage
