"""Deterministic mapping shortlist and semantic guardrails, independent of model calls."""
from __future__ import annotations
import difflib
from typing import Any
from src.domain import SourceDiseaseCategory, StandardDisease, DiseaseConceptRelation
from src.ontology.mapping_identity import normalize_source_text

def _deterministic_shortlist(
    category: SourceDiseaseCategory, standards: list[StandardDisease]
) -> list[dict[str, Any]]:
    source = normalize_source_text(category.canonical_source_label)
    scored = []
    for disease in standards:
        target = normalize_source_text(disease.standard_name_en)
        score = difflib.SequenceMatcher(None, source, target).ratio()
        if source == target:
            score = 1.0
        source_tokens, target_tokens = set(source.split()), set(target.split())
        if source_tokens and target_tokens:
            score = max(score, len(source_tokens & target_tokens) / len(source_tokens | target_tokens))
        scored.append(
            {
                "disease_id": disease.disease_id,
                "name_en": disease.standard_name_en,
                "name_zh": disease.standard_name_zh,
                "icd_10": disease.icd_10,
                "score": round(score, 6),
            }
        )
    return sorted(scored, key=lambda item: (-item["score"], item["disease_id"]))[:12]


def _unique_exact_match(shortlist: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact_matches = [
        item for item in shortlist if float(item.get("score") or 0) == 1.0
    ]
    return exact_matches[0] if len(exact_matches) == 1 else None


def _apply_interpreted_name_guardrail(
    output: dict[str, Any], standards: list[StandardDisease]
) -> dict[str, Any]:
    """Surface an exact catalogue match when a model selected a broader target.

    Models can correctly translate a source term but still prefer an umbrella
    concept (for example German ``Typhus`` -> "Typhoid fever" while selecting
    "Typhoid and Paratyphoid fever").  The interpreted source meaning is useful
    evidence, but it is not authority.  This guardrail makes the exact-name
    concept the first candidate and retains the model's broader choice as a
    competing candidate the reviewer must weigh.
    """

    candidates = output.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return output
    by_name: dict[str, list[StandardDisease]] = {}
    for disease in standards:
        by_name.setdefault(normalize_source_text(disease.standard_name_en), []).append(disease)

    first = candidates[0] if isinstance(candidates[0], dict) else {}
    interpreted = normalize_source_text(first.get("interpreted_name_en"))
    exact_matches = by_name.get(interpreted, [])
    if len(exact_matches) != 1:
        return output
    exact = exact_matches[0]
    if str(first.get("target_code") or "").upper() == exact.disease_id:
        return output

    guarded = dict(first)
    guarded.update(
        {
            "candidate_kind": "existing_concept",
            "target_code": exact.disease_id,
            "mapping_relation": "exact",
            "comparability": "conditional",
            "confidence_score": min(float(first.get("confidence_score") or 0), 0.75),
            "reasoning": (
                f"The models interpreted the source term as {exact.standard_name_en!r}, "
                f"which exactly matches catalogue concept {exact.disease_id}. The model's "
                "different umbrella target is retained for explicit scope review."
            ),
            "evidence": list(first.get("evidence") or [])
            + ["Deterministic guardrail: interpreted source meaning exactly matches one catalogue name."],
        }
    )
    retained = dict(first)
    retained["comparability"] = "unknown"
    retained["confidence_score"] = min(float(first.get("confidence_score") or 0), 0.65)
    retained["reasoning"] = (
        "Catalogue-scope conflict: an exact concept exists for the model's interpreted source "
        "meaning. The peer reviewer must choose between the exact and umbrella concepts. "
        + str(first.get("reasoning") or "")
    )
    return {"candidates": [guarded, retained] + [item for item in candidates[1:2] if isinstance(item, dict)]}


def _apply_concept_relation_guardrail(
    output: dict[str, Any],
    standards: list[StandardDisease],
    relations: list[DiseaseConceptRelation],
) -> dict[str, Any]:
    """Normalize hierarchy direction using the reviewed concept graph."""

    candidates = output.get("candidates")
    if not isinstance(candidates, list):
        return output
    by_name: dict[str, list[str]] = {}
    for disease in standards:
        by_name.setdefault(normalize_source_text(disease.standard_name_en), []).append(
            disease.disease_id
        )
    relation_index = {
        (item.subject_disease_id, item.object_disease_id): item
        for item in relations
        if item.is_hierarchical and item.assertion_status == "approved"
    }
    normalized: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        interpreted_ids = by_name.get(
            normalize_source_text(item.get("interpreted_name_en")), []
        )
        target = str(item.get("target_code") or "").upper()
        if len(interpreted_ids) == 1 and target and target != interpreted_ids[0]:
            interpreted_id = interpreted_ids[0]
            relation = relation_index.get((interpreted_id, target))
            inverse = relation_index.get((target, interpreted_id))
            if relation is not None:
                item["mapping_relation"] = "narrower"
                item["evidence"] = list(item.get("evidence") or []) + [
                    f"Concept-graph guardrail: {interpreted_id} is a hierarchical component/form of {target}; SOURCE is narrower than TARGET."
                ]
            elif inverse is not None:
                item["mapping_relation"] = "broader"
                item["evidence"] = list(item.get("evidence") or []) + [
                    f"Concept-graph guardrail: {target} is a hierarchical component/form of {interpreted_id}; SOURCE is broader than TARGET."
                ]
        normalized.append(item)
    return {**output, "candidates": normalized}
