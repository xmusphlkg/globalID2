"""Bounded source metadata retained by the Research Radar database."""

from __future__ import annotations

from typing import Any, Mapping


SOURCE_PAYLOAD_SCHEMA_VERSION = 1
_MAX_GENERIC_ITEMS = 250
_MAX_GENERIC_STRING = 2_000
_CROSSREF_MARKERS = frozenset({"DOI", "indexed", "container-title"})
_AUXILIARY_PROVIDER_KEYS = frozenset({
    "biorxiv",
    "controlled_discovery_origins",
    "elsevier",
    "europe_pmc",
    "official_guidance",
    "openalex",
    "pubmed",
    "pubmed_efetch",
    "rss",
    "springer_nature",
    "unpaywall",
})


def _bounded_json(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [_MAX_GENERIC_ITEMS]
    if budget[0] <= 0 or depth > 5:
        return None
    budget[0] -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_GENERIC_STRING]
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if budget[0] <= 0:
                break
            key = str(raw_key)[:120]
            bounded = _bounded_json(raw_value, depth=depth + 1, budget=budget)
            if bounded is not None:
                output[key] = bounded
        return output
    if isinstance(value, (list, tuple)):
        output = []
        for item in value:
            if budget[0] <= 0:
                break
            bounded = _bounded_json(item, depth=depth + 1, budget=budget)
            if bounded is not None:
                output.append(bounded)
        return output
    return str(value)[:_MAX_GENERIC_STRING]


def _compact_crossref(payload: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in ("DOI", "indexed", "container-title", "type", "subtype", "relation"):
        if key in payload:
            value = _bounded_json(payload.get(key))
            if value is not None:
                output[key] = value
    updates = []
    for raw in payload.get("update-to") or []:
        if not isinstance(raw, Mapping):
            continue
        item = {
            key: str(raw.get(key))[:500]
            for key in ("type", "DOI", "doi", "label", "updated")
            if raw.get(key)
        }
        if item:
            updates.append(item)
        if len(updates) >= 20:
            break
    if updates:
        output["update-to"] = updates
    discovery = payload.get("_research_radar_discovery")
    if isinstance(discovery, Mapping):
        output["_research_radar_discovery"] = _bounded_json(discovery)
    return output


def _compact_europe_pmc(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    output: dict[str, Any] = {}
    for key in (
        "pmid",
        "pmcid",
        "source",
        "isOpenAccess",
        "meshHeadingList",
        "keywordList",
        "pubTypeList",
        "_research_radar_discovery",
    ):
        if key in payload:
            bounded = _bounded_json(payload.get(key))
            if bounded is not None:
                output[key] = bounded
    return output


def _compact_pubmed(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    output: dict[str, Any] = {}
    for key in ("uid", "pmid", "pubtype", "source"):
        if key in payload:
            bounded = _bounded_json(payload.get(key))
            if bounded is not None:
                output[key] = bounded
    return output


def compact_source_payload(value: Any) -> dict[str, Any]:
    """Return a bounded audit payload while preserving policy-critical evidence."""

    payload = value if isinstance(value, Mapping) else {}
    output = _compact_crossref(payload)
    is_crossref_message = bool(_CROSSREF_MARKERS.intersection(payload))
    for key, raw in payload.items():
        if key in output or key in {
            "DOI",
            "indexed",
            "container-title",
            "type",
            "subtype",
            "relation",
            "update-to",
            "_research_radar_discovery",
        }:
            continue
        # Crossref candidates store the complete provider message at the top
        # level. Keep only the explicit Crossref audit projection above plus
        # recognized enrichment-provider namespaces merged into that record.
        if is_crossref_message and key not in _AUXILIARY_PROVIDER_KEYS:
            continue
        if key == "europe_pmc":
            output[key] = _compact_europe_pmc(raw)
        elif key in {"pubmed", "pubmed_efetch"}:
            output[key] = _compact_pubmed(raw)
        elif key == "controlled_discovery_origins":
            origins = [
                _bounded_json(item)
                for item in (raw or [])[:20]
                if isinstance(item, Mapping)
            ]
            if origins:
                output[key] = origins
        else:
            bounded = _bounded_json(raw)
            if bounded is not None:
                output[str(key)[:120]] = bounded
    return output


__all__ = [
    "SOURCE_PAYLOAD_SCHEMA_VERSION",
    "compact_source_payload",
]
