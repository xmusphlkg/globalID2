"""Citation normalization for disease knowledge briefs."""
from __future__ import annotations

import re
from typing import Any, Literal


KNOWLEDGE_CITATION_FIELDS = (
    "brief",
    "definition",
    "clinical_features",
    "clinical_summary",
    "epidemiology",
    "transmission",
    "prevention",
    "surveillance_note",
    "risk_groups",
)

MarkerMode = Literal["auto", "position", "source_id"]

_CITATION_GROUP_RE = re.compile(r"(?:\[\d+\])+")
_CITATION_NUMBER_RE = re.compile(r"\[(\d+)\]")


def normalize_knowledge_citations(
    payload: dict[str, Any],
    *,
    marker_mode: MarkerMode = "auto",
) -> dict[str, Any]:
    """Return a copy of a brief payload with sequential citation markers.

    Older prompts asked the model to cite database source ids directly. The
    public page, however, needs a compact reference list numbered by display
    order. This normalizer accepts either style and makes both the text and the
    `source_attribution` list use a single 1-based `citation_index`.
    """

    return normalize_knowledge_citation_group([payload], marker_mode=marker_mode)[0]


def normalize_knowledge_citation_group(
    payloads: list[dict[str, Any]],
    *,
    marker_mode: MarkerMode = "auto",
) -> list[dict[str, Any]]:
    """Normalize several language payloads against one shared reference order."""

    normalized_payloads = [dict(payload) for payload in payloads]
    raw_sources = []
    for payload in normalized_payloads:
        raw_sources = payload.get("source_attribution") or []
        if raw_sources:
            break

    source_entries = _normalize_source_entries(raw_sources)
    if not source_entries:
        return [_strip_unmatched_citations(payload) for payload in normalized_payloads]

    text_values = [
        str(payload.get(field) or "")
        for payload in normalized_payloads
        for field in KNOWLEDGE_CITATION_FIELDS
        if payload.get(field)
    ]
    markers = _extract_markers(text_values)
    mode = _resolve_marker_mode(markers, source_entries, marker_mode)

    ordered_keys: list[str] = []
    unknown_markers: set[int] = set()

    def remember_marker(marker: int) -> None:
        key = _resolve_marker(marker, source_entries, mode)
        if key is None:
            unknown_markers.add(marker)
            return
        if key not in ordered_keys:
            ordered_keys.append(key)

    for value in text_values:
        for marker in _extract_markers([value]):
            remember_marker(marker)

    for entry in source_entries:
        if entry["_key"] not in ordered_keys:
            ordered_keys.append(entry["_key"])

    citation_index_by_key = {key: index + 1 for index, key in enumerate(ordered_keys)}
    entry_by_key = {entry["_key"]: entry for entry in source_entries}

    normalized_sources = []
    for key in ordered_keys:
        entry = dict(entry_by_key[key]["source"])
        source_id = entry_by_key[key].get("source_id")
        if source_id is not None:
            entry["source_id"] = source_id
        entry["citation_index"] = citation_index_by_key[key]
        normalized_sources.append(entry)

    for payload in normalized_payloads:
        for field in KNOWLEDGE_CITATION_FIELDS:
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                payload[field] = _rewrite_citation_markers(
                    value,
                    source_entries=source_entries,
                    citation_index_by_key=citation_index_by_key,
                    mode=mode,
                    unknown_markers=unknown_markers,
                )

        payload["source_attribution"] = normalized_sources
        payload["source_ids"] = [
            entry.get("source_id") or entry.get("id")
            for entry in normalized_sources
            if entry.get("source_id") is not None or entry.get("id") is not None
        ]
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            "citation_style": "sequential",
            "citation_version": 2,
            "citation_marker_mode": mode,
            "citation_unknown_markers": sorted(unknown_markers),
        }
    return normalized_payloads


def _normalize_source_entries(sources: Any) -> list[dict[str, Any]]:
    if not isinstance(sources, list):
        return []

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(sources):
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        source_id = _int_or_none(source.get("source_id"))
        if source_id is None:
            source_id = _int_or_none(source.get("id"))
        key = _source_key(source, source_id, index)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "_key": key,
                "source": source,
                "source_id": source_id,
                "position": index + 1,
                "citation_index": _int_or_none(source.get("citation_index")),
            }
        )
    return entries


def _source_key(source: dict[str, Any], source_id: int | None, index: int) -> str:
    if source_id is not None:
        return f"id:{source_id}"
    url = str(source.get("resolved_url") or source.get("url") or "").strip()
    if url:
        return f"url:{url}"
    title = str(source.get("title") or source.get("source_name") or "").strip()
    if title:
        return f"title:{title}"
    return f"index:{index}"


def _resolve_marker_mode(
    markers: list[int],
    source_entries: list[dict[str, Any]],
    requested: MarkerMode,
) -> Literal["position", "source_id"]:
    if requested in {"position", "source_id"}:
        return requested
    if not markers:
        return "position"

    source_ids = {entry["source_id"] for entry in source_entries if entry.get("source_id") is not None}
    source_count = len(source_entries)
    unique_markers = set(markers)

    has_source_id_outside_position_range = any(
        marker in source_ids and not 1 <= marker <= source_count
        for marker in unique_markers
    )
    if has_source_id_outside_position_range:
        return "source_id"

    has_position_only_marker = any(
        1 <= marker <= source_count and marker not in source_ids
        for marker in unique_markers
    )
    if has_position_only_marker:
        return "position"

    if unique_markers.issubset(set(range(1, source_count + 1))):
        return "position"

    return "source_id"


def _resolve_marker(
    marker: int,
    source_entries: list[dict[str, Any]],
    mode: Literal["position", "source_id"],
) -> str | None:
    if mode == "position":
        for entry in source_entries:
            if entry["position"] == marker or entry.get("citation_index") == marker:
                return entry["_key"]
        return None

    for entry in source_entries:
        if entry.get("source_id") == marker:
            return entry["_key"]
    for entry in source_entries:
        if entry.get("citation_index") == marker:
            return entry["_key"]
    return None


def _rewrite_citation_markers(
    value: str,
    *,
    source_entries: list[dict[str, Any]],
    citation_index_by_key: dict[str, int],
    mode: Literal["position", "source_id"],
    unknown_markers: set[int],
) -> str:
    def replace_group(match: re.Match[str]) -> str:
        display_numbers: list[int] = []
        for marker_text in _CITATION_NUMBER_RE.findall(match.group(0)):
            marker = int(marker_text)
            key = _resolve_marker(marker, source_entries, mode)
            if key is None:
                unknown_markers.add(marker)
                continue
            display_number = citation_index_by_key.get(key)
            if display_number is not None and display_number not in display_numbers:
                display_numbers.append(display_number)
        return "".join(f"[{number}]" for number in display_numbers)

    return _CITATION_GROUP_RE.sub(replace_group, value)


def _strip_unmatched_citations(payload: dict[str, Any]) -> dict[str, Any]:
    markers: set[int] = set()
    for field in KNOWLEDGE_CITATION_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            markers.update(_extract_markers([value]))
            payload[field] = _CITATION_GROUP_RE.sub("", value)
    if markers:
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            "citation_style": "none",
            "citation_unknown_markers": sorted(markers),
        }
    return payload


def _extract_markers(values: list[str]) -> list[int]:
    markers: list[int] = []
    for value in values:
        for match in _CITATION_NUMBER_RE.finditer(value):
            markers.append(int(match.group(1)))
    return markers


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
