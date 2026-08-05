"""Small deterministic helpers shared by agent workflow modules."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

QUERY_STOPWORDS = {
    "a", "an", "and", "are", "about", "for", "from", "how", "is", "in", "of", "on", "or",
    "the", "to", "what", "when", "where", "which", "who", "why", "with", "please", "need",
    "use", "task", "prompt", "data", "search", "analyze", "analysis",
}


def compact_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def stable_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def safe_json(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def extract_keywords(prompt: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", prompt.lower())
    keywords: list[str] = []
    for token in tokens:
        if token not in QUERY_STOPWORDS and token not in keywords:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def coerce_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def unique_items(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for item in values:
        if item is None:
            continue
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
