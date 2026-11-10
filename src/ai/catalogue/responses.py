"""Provider response normalization without promoting hidden reasoning to output."""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*?</(?:think|thinking)>",
    re.IGNORECASE | re.DOTALL,
)

_THINK_OPEN_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                kind = str(item.get("type") or "").lower()
                if kind in {"thinking", "reasoning", "redacted_thinking"}:
                    continue
                text = item.get("text")
                if text is None:
                    text = item.get("content")
            else:
                kind = str(getattr(item, "type", "") or "").lower()
                if kind in {"thinking", "reasoning", "redacted_thinking"}:
                    continue
                text = getattr(item, "text", None)
                if text is None:
                    text = getattr(item, "content", None)
            if isinstance(text, str):
                parts.append(text)
            elif text is not None and not isinstance(text, (dict, list)):
                parts.append(str(text))
        return "\n".join(part for part in parts if part)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return str(content)


def visible_assistant_text(content: Any) -> str:
    """Return the answer text, without a reasoning trace.

    Gateways expose reasoning as ``reasoning_content``, as typed content blocks,
    or inline as ``<think>`` / ``<thinking>`` tags (the formats used by Kiro-Go
    and New API). The answer is the remaining text. Reasoning is not promoted
    into the answer.
    """
    text = _THINK_BLOCK_RE.sub("", _content_to_text(content))
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def _openai_response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return visible_assistant_text(getattr(message, "content", None))
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text

    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return visible_assistant_text(message.get("content"))
                text = first.get("text")
                if isinstance(text, str):
                    return text
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        return json.dumps(response, ensure_ascii=False)

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    return str(response)


def _looks_like_html(text: str) -> bool:
    normalized = text.lstrip().lower()
    return normalized.startswith("<!doctype html") or normalized.startswith("<html")


def _response_preview(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def _model_ids_from_catalogue(payload: Any) -> list[str]:
    """Extract stable model identifiers from OpenAI-compatible model listings."""
    items = getattr(payload, "data", None)
    if isinstance(payload, dict):
        items = payload.get("data", items)
    if not isinstance(items, list):
        return []

    model_ids: list[str] = []
    for item in items:
        identifier = (
            item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        )
        value = str(identifier or "").strip()
        if value and value not in model_ids:
            model_ids.append(value)
    return sorted(model_ids)
