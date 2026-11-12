"""Evidence requirements for disease-relevance screening.

Keyword and provider-subject matches identify candidates. They do not prove
that an article studies a disease; that requires a contextual assessment.
"""
from __future__ import annotations

import re
from typing import Any

SCREENING_POLICY_VERSION = "disease-relevance-screen.v1"
_SPACE = re.compile(r"\s+")
_QUOTED = re.compile(r'["“]([^"”]{15,160})["”]')


def _normalize(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip().casefold()


def controlled_subjects(article: Any) -> list[dict[str, Any]]:
    """Return bounded, attributed indexing terms, never provider payloads."""
    payload = getattr(article, "source_payload", None) or {}
    if not isinstance(payload, dict):
        return []
    epmc = payload.get("europe_pmc") or {}
    if not isinstance(epmc, dict):
        return []
    headings = (epmc.get("meshHeadingList") or {})
    headings = headings.get("meshHeading") or [] if isinstance(headings, dict) else []
    if not isinstance(headings, list):
        headings = [headings]
    result = []
    for item in headings[:50]:
        if not isinstance(item, dict):
            continue
        descriptor = item.get("descriptorName")
        if isinstance(descriptor, dict):
            descriptor = descriptor.get("$") or descriptor.get("name")
        if not isinstance(descriptor, str) or not descriptor.strip():
            continue
        result.append({"source": "europe_pmc.mesh", "term": descriptor.strip()[:180],
                       "major_topic": str(item.get("majorTopic_YN") or "").upper() == "Y"})
    return result


def initial_screen(article: Any) -> dict[str, Any]:
    """Describe available evidence; never make a final relevance decision."""
    abstract = _normalize(getattr(article, "abstract_text", None))
    metadata = getattr(article, "metadata_", None) or {}
    classification = metadata.get("classification_evidence") or {} if isinstance(metadata, dict) else {}
    diseases = classification.get("diseases") or {} if isinstance(classification, dict) else {}
    subjects = controlled_subjects(article)
    return {
        "policy_version": SCREENING_POLICY_VERSION,
        "status": "contextual_review" if len(abstract) >= 80 else "needs_more_evidence",
        "has_abstract_context": len(abstract) >= 80,
        "candidate_disease_ids": sorted(str(key) for key in diseases) if isinstance(diseases, dict) else [],
        "controlled_subjects": subjects,
    }


def decision_is_grounded(article: Any, reviewed: Any) -> bool:
    """Require a short verbatim supporting excerpt from the available abstract.

    This confirms traceability, not semantic correctness. A model may still
    misunderstand a genuine quotation, so peer review remains necessary.
    """
    # The review prompt exposes at most 3,600 normalized characters.
    abstract = _normalize(getattr(article, "abstract_text", None))[:3600]
    if len(abstract) < 80:
        return False
    payload = getattr(reviewed, "payload", None) or {}
    reasons = payload.get("reasons") or [] if isinstance(payload, dict) else []
    if not isinstance(reasons, list):
        return False
    return any(_normalize(quote) in abstract for reason in reasons if isinstance(reason, str)
               for quote in _QUOTED.findall(reason))
