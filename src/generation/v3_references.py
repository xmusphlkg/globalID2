"""Reference registry helpers for analytical v3 reports."""
from __future__ import annotations

import re
from typing import Any, Dict, List


def citation_index(value: Any, fallback: int) -> int:
    try:
        index = int(value)
        return index if index > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def reference_identity(source: Dict[str, Any], fallback: int) -> str:
    for key in ("source_id", "id"):
        value = source.get(key)
        if value not in (None, ""):
            return f"id:{value}"
    for key in ("resolved_url", "url"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return f"url:{value.strip()}"
    title = source.get("title") or source.get("source_name")
    if isinstance(title, str) and title.strip():
        return f"title:{title.strip().lower()}"
    return f"source:{fallback}"


def build_reference_registry(evidence_packet: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Dict[int, int]]]:
    """Create global report reference numbers from per-disease knowledge briefs."""
    references: List[Dict[str, Any]] = []
    global_by_key: Dict[str, int] = {}
    citation_maps: Dict[str, Dict[int, int]] = {}
    diseases = sorted(
        evidence_packet.get("diseases") or [],
        key=lambda row: row.get("risk", {}).get("score", 0),
        reverse=True,
    )[:8]

    for item in diseases:
        disease_key = str(item.get("disease_id") or item.get("name_en") or item.get("name_zh") or "")
        knowledge = item.get("knowledge_context") or {}
        local_map: Dict[int, int] = {}
        for position, source in enumerate(knowledge.get("source_attribution") or [], 1):
            if not isinstance(source, dict):
                continue
            key = reference_identity(source, position)
            if key not in global_by_key:
                global_by_key[key] = len(references) + 1
                source_copy = dict(source)
                source_copy["citation_index"] = global_by_key[key]
                references.append(source_copy)
            local_index = citation_index(source.get("citation_index"), position)
            local_map[local_index] = global_by_key[key]
        if disease_key:
            citation_maps[disease_key] = local_map

    return references, citation_maps


def remap_inline_citations(text: Any, citation_map: Dict[int, int]) -> str:
    rendered = str(text or "")
    if not rendered or not citation_map:
        return rendered

    def replace_marker(match: re.Match[str]) -> str:
        marker = int(match.group(1))
        return f"[{citation_map.get(marker, marker)}]"

    return re.sub(r"\[(\d+)\]", replace_marker, rendered)
