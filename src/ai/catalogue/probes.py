"""Health probe transports with explicit SDK factories and bounded request options.

These functions execute probes but never update catalogue rows or decide whether
another probe should run. That orchestration stays in the model-center service.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from src.core import get_logger

from .options import (
    ANTHROPIC_PROTOCOL_MAX_TOKENS,
    _openai_base_url_candidates,
    anthropic_sdk_base_url,
    drop_rejected_optional_parameter,
    route_completion_extra_body,
)
from .responses import (
    _looks_like_html,
    _openai_response_text,
    _response_preview,
    visible_assistant_text,
)
from .routes import _positive_int

logger = get_logger(__name__)


@asynccontextmanager
async def client_session(factory: Callable[..., Any], **options: Any):
    """Close the SDK's HTTP connection pool after this bounded operation."""
    client = factory(**options)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()


_MODEL_TEST_MARKER = "globalid-model-test-ok"

_MODEL_TEST_PROMPT = (
    "This is a production model-center health check. "
    f"Reply with exactly this text and nothing else: {_MODEL_TEST_MARKER}"
)

_STRUCTURED_MODEL_TEST_PROMPT = (
    "This is a production model-center literature workload probe. Return JSON only. "
    "Use exactly this top-level shape: "
    '{"status":"globalid-structured-probe-ok","items":[...]}. '
    "Create exactly three items with integer ids 1, 2, and 3. Each item must contain "
    "a title, a factual summary of at least 60 characters, and a confidence value from "
    "0 to 1. Summarize these synthetic findings: (1) wastewater surveillance can lead "
    "clinical case trends; (2) vaccination reduces severe outcomes; (3) reporting delay "
    "biases the newest observations. Do not use markdown or add unsupported facts."
)


def _validate_test_response(text: str) -> None:
    value = text.strip()
    if not value:
        raise RuntimeError("Chat completion returned an empty assistant response")
    if _looks_like_html(value):
        raise RuntimeError(
            "Chat completion returned an HTML page instead of an assistant response. "
            "Check the provider base_url; OpenAI-compatible gateways usually require /v1."
        )
    if _MODEL_TEST_MARKER not in value.lower():
        raise RuntimeError(
            f"Chat completion did not echo the expected test marker. Response preview: {_response_preview(value)}"
        )


def _validate_structured_test_response(text: str) -> None:
    value = text.strip()
    if not value:
        raise RuntimeError(
            "Structured workload probe returned an empty assistant response"
        )
    if _looks_like_html(value):
        raise RuntimeError("Structured workload probe returned HTML instead of JSON")

    # Literature enrichment accepts JSON wrapped in a Markdown fence (and a
    # small amount of surrounding prose), which is common for otherwise valid
    # model responses. Keep the health probe aligned with that production
    # contract so formatting alone cannot strand a capable route as degraded.
    fenced = re.search(
        r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        value = fenced.group(1)
    else:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Structured workload probe did not return valid JSON"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "globalid-structured-probe-ok"
    ):
        raise RuntimeError(
            "Structured workload probe returned an unexpected JSON payload"
        )
    items = payload.get("items")
    if (
        not isinstance(items, list)
        or len(items) != 3
        or not all(isinstance(item, dict) for item in items)
    ):
        raise RuntimeError(
            "Structured workload probe must return exactly three evidence items"
        )
    if [item.get("id") for item in items] != [1, 2, 3]:
        raise RuntimeError(
            "Structured workload probe returned unexpected evidence item ids"
        )
    for item in items:
        if not str(item.get("title") or "").strip():
            raise RuntimeError("Structured workload probe omitted an evidence title")
        if len(str(item.get("summary") or "").strip()) < 60:
            raise RuntimeError(
                "Structured workload probe returned an incomplete evidence summary"
            )
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Structured workload probe returned an invalid confidence value"
            ) from exc
        if not 0 <= confidence <= 1:
            raise RuntimeError(
                "Structured workload probe returned confidence outside the 0..1 range"
            )


def _test_timeout_seconds(route: dict[str, Any], *, structured: bool = False) -> float:
    # The marker reply is a few tokens; a minute is ample even for a reasoning
    # model. The structured probe writes three summaries and keeps the longer
    # production-like deadline. A configured value overrides both.
    default = 180 if structured else 60
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    model_params = route.get("extra_params")
    model_params = model_params if isinstance(model_params, dict) else {}
    raw = model_params.get("health_check_timeout_seconds")
    if raw in (None, ""):
        raw = provider_config.get("health_check_timeout_seconds", default)
    try:
        return float(max(10, min(600, int(raw))))
    except (TypeError, ValueError):
        return float(default)


async def probe_openai(
    route: dict[str, Any],
    model_name: str,
    *,
    client_factory: Callable[..., Any],
    structured: bool = False,
) -> dict[str, Any]:
    from src.ai.runtime import require_dispatcher

    require_dispatcher()
    base_urls = _openai_base_url_candidates(str(route.get("base_url") or ""))

    last_error: Exception | None = None
    for candidate_base_url in base_urls:
        async with client_session(
            client_factory,
            api_key=route.get("api_key"),
            base_url=candidate_base_url,
            default_headers=(route.get("extra_headers") or None),
            timeout=_test_timeout_seconds(route, structured=structured),
            max_retries=0,
        ) as client:
            prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are validating an evidence-summarization model used by a public-health literature pipeline.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
            }
            extra_body = route_completion_extra_body(route)
            if extra_body:
                payload["extra_body"] = extra_body
            dropped_parameters: set[str] = set()
            try:
                while True:
                    try:
                        response = await client.chat.completions.create(**payload)
                        break
                    except Exception as exc:
                        if len(
                            dropped_parameters
                        ) >= 4 or not drop_rejected_optional_parameter(
                            exc, payload, dropped_parameters
                        ):
                            raise
                        logger.warning(
                            "Probe dropped unsupported parameter for model '{}': {}",
                            model_name,
                            ", ".join(sorted(dropped_parameters)),
                        )
            except Exception as exc:
                last_error = exc
                # An alternate API path can fix a missing endpoint, but cannot fix
                # a completed response, rate limit, timeout, or provider failure.
                if getattr(exc, "status_code", None) != 404:
                    raise
                continue

            text = _openai_response_text(response)
            if not text.strip():
                first = (
                    response.choices[0] if getattr(response, "choices", None) else None
                )
                finish_reason = getattr(first, "finish_reason", None)
                completion_tokens = getattr(
                    getattr(response, "usage", None), "completion_tokens", None
                )
                raise RuntimeError(
                    "Chat completion produced no final assistant content "
                    f"(finish_reason={finish_reason!r}, completion_tokens={completion_tokens!r})"
                )
            if structured:
                _validate_structured_test_response(text)
            else:
                _validate_test_response(text)
            return {
                "base_url": candidate_base_url or "default-openai-base-url",
                "response_preview": _response_preview(text),
            }

    if last_error is not None:
        raise last_error
    raise RuntimeError("Chat completion returned no usable response")


async def probe_anthropic(
    route: dict[str, Any],
    model_name: str,
    *,
    client_factory: Callable[..., Any],
    structured: bool = False,
) -> dict[str, Any]:
    from src.ai.runtime import require_dispatcher

    require_dispatcher()
    prompt = _STRUCTURED_MODEL_TEST_PROMPT if structured else _MODEL_TEST_PROMPT
    async with client_session(
        client_factory,
        api_key=route.get("api_key"),
        base_url=anthropic_sdk_base_url(route.get("base_url")),
        default_headers=(route.get("extra_headers") or None),
        timeout=_test_timeout_seconds(route, structured=structured),
        max_retries=0,
    ) as client:
        response = await client.messages.create(
            model=model_name,
            system="You are validating an evidence-summarization model used by a public-health literature pipeline.",
            messages=[{"role": "user", "content": prompt}],
            # The marker reply is a handful of tokens; asking for the protocol
            # ceiling only lets a slow gateway hold the probe open.
            max_tokens=ANTHROPIC_PROTOCOL_MAX_TOKENS if structured else 1024,
            temperature=0,
        )
        text = visible_assistant_text(
            [
                block
                for block in response.content
                if getattr(block, "type", None) == "text"
                and getattr(block, "text", None)
            ]
        )
        if structured:
            _validate_structured_test_response(text)
        else:
            _validate_test_response(text)
        return {"response_preview": _response_preview(text)}


async def probe_native_file(
    route: dict[str, Any],
    *,
    openai_factory: Callable[..., Any],
    anthropic_factory: Callable[..., Any],
) -> bool | None:
    """Send a PDF carrying a random marker and check that the model reads it.

    Returns True when the reply contains the marker, False when the model answered
    without it or the provider rejected the file, and None when the outcome says
    nothing about file support (timeout, rate limit, server fault).
    """
    import secrets

    from src.ai.attachments import (
        Attachment,
        as_native_content,
        build_probe_pdf,
        looks_like_file_rejection,
    )
    from src.ai.runtime import require_dispatcher

    require_dispatcher()
    marker = "GIDS" + secrets.token_hex(6)
    style = str(route.get("api_style") or "openai_compatible").lower()
    model_name = str(route.get("model_name") or "")
    # The attachment text is empty so the marker can only come from the file itself.
    content = as_native_content(
        style,
        "The attached PDF contains one short code. Reply with that code only.",
        [Attachment(name="probe.pdf", text="", data=build_probe_pdf(marker))],
    )
    timeout = _test_timeout_seconds(route)
    try:
        if style == "anthropic":
            async with client_session(
                anthropic_factory,
                api_key=route.get("api_key"),
                base_url=anthropic_sdk_base_url(route.get("base_url")),
                default_headers=(route.get("extra_headers") or None),
                timeout=timeout,
                max_retries=0,
            ) as client:
                response = await client.messages.create(
                    model=model_name,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=256,
                    temperature=0,
                )
                reply = visible_assistant_text(
                    [b for b in response.content if getattr(b, "type", None) == "text"]
                )
        else:
            async with client_session(
                openai_factory,
                api_key=route.get("api_key"),
                base_url=(
                    _openai_base_url_candidates(str(route.get("base_url") or ""))
                    or [None]
                )[0],
                default_headers=(route.get("extra_headers") or None),
                timeout=timeout,
                max_retries=0,
            ) as client:
                payload: dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0,
                }
                extra_body = route_completion_extra_body(route)
                if extra_body:
                    payload["extra_body"] = extra_body
                reply = _openai_response_text(
                    await client.chat.completions.create(**payload)
                )
    except Exception as exc:
        if looks_like_file_rejection(exc):
            return False
        logger.info("Native file probe inconclusive for '{}': {}", model_name, exc)
        return None
    return marker.lower() in reply.lower()


def health_check_concurrency(route: dict[str, Any]) -> int:
    """Use a small provider-specific batch to catch concurrency-only failures."""
    provider_config = route.get("extra_config")
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    configured = provider_config.get("health_check_concurrency")
    try:
        provider_maximum = max(1, int(route.get("runtime_provider_max_capacity") or 1))
    except (TypeError, ValueError):
        provider_maximum = 1
    return _positive_int(configured, min(2, provider_maximum), maximum=provider_maximum)
