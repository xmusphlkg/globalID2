"""Disease catalogue visibility and knowledge publication state.

Catalogue fields are identifiers and routing metadata, not medical evidence.
They must never be expanded into disease prose when a source-grounded profile
cannot be generated.  Historical catalogue fallback rows are recognized only
so they can be classified as blocked and kept out of public profiles.
"""
from __future__ import annotations

from typing import Any, Iterable


NON_PUBLIC_DISEASE_IDS = frozenset({"D999"})
LEGACY_CATALOGUE_BRIEF_TIER = "catalogue_fallback"
NON_PUBLIC_DESCRIPTION_PHRASES = (
    "aggregate total",
    "deprecated duplicate",
)


def public_disease_page_exclusion_reason(disease: Any) -> str | None:
    """Explain why a catalogue entity should not receive a public page."""
    disease_id = _value(disease, "disease_id").upper()
    category = _value(disease, "category").lower()
    name_en = _value(disease, "name_en").lower() or _value(disease, "standard_name_en").lower()
    description = _value(disease, "description").lower()

    if disease_id in NON_PUBLIC_DISEASE_IDS:
        return "non_public_disease_id"
    if category == "summary":
        return "summary_category"
    if name_en in {"total", "summary"}:
        return "summary_name"
    if any(
        phrase in description or phrase in name_en
        for phrase in NON_PUBLIC_DESCRIPTION_PHRASES
    ):
        return "deprecated_or_aggregate_description"
    return None


def should_generate_public_disease_page(disease: Any) -> bool:
    """Return True when a disease should appear in public site/page exports."""
    return public_disease_page_exclusion_reason(disease) is None


def knowledge_brief_publication_tier(brief: Any) -> str:
    """Classify a stored brief without treating missing evidence as a fallback."""
    status = _value(brief, "status").lower()
    metadata = _brief_metadata(brief)
    if metadata.get("brief_tier") == LEGACY_CATALOGUE_BRIEF_TIER:
        return "blocked"
    if status == "published":
        return "published"
    if status in {"draft", "awaiting_evidence"}:
        return "automating"
    return status or "blocked"


def knowledge_brief_block_reason(brief: Any) -> str | None:
    """Return the current block reason, accepting the legacy metadata key."""
    metadata = _brief_metadata(brief)
    value = metadata.get("block_reason") or metadata.get("fallback_reason")
    if value in (None, ""):
        return None
    return str(value)


def resolve_disease_knowledge_status(briefs: Iterable[Any]) -> str:
    """Resolve disease-level status as published, reviewable, or blocked."""
    has_review = False
    has_automation = False
    has_blocked = False

    for brief in briefs:
        tier = knowledge_brief_publication_tier(brief)
        if tier == "published":
            return "published"
        if tier == "blocked":
            has_blocked = True
        elif tier == "automating":
            has_automation = True
        elif tier:
            has_review = True

    if has_review:
        return "requires_review"
    if has_automation:
        return "automating"
    if has_blocked:
        return "blocked"
    return "blocked"


def _value(obj: Any, key: str) -> str:
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return str(value).strip() if value not in (None, "") else ""


def _brief_metadata(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        metadata = obj.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        metadata = obj.get("metadata_")
        if isinstance(metadata, dict):
            return metadata
        return {}
    metadata = getattr(obj, "metadata_", None)
    return metadata if isinstance(metadata, dict) else {}
