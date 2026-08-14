"""Transparent first-pass classification and discovery ranking."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from .types import ArticleCandidate, Classification, Match


_NON_CONCEPT_DISEASE_IDS = {"D999"}
_NON_CONCEPT_DISEASE_NAMES = {"all", "other", "total", "unknown", "unspecified"}


def _contains(text: str, term: str) -> bool:
    normalized = term.strip().lower()
    if len(normalized) < 3:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None


def _term_matches(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in dict.fromkeys(terms) if _contains(text, term)]


def classify_candidate(
    candidate: ArticleCandidate,
    *,
    diseases: list[dict[str, Any]],
    countries: list[dict[str, str]],
    taxonomy: dict[str, Any],
    now: datetime | None = None,
    auto_publish_min_score: float = 0.86,
) -> Classification:
    title_text = candidate.title.lower()
    body_text = f"{candidate.title} {candidate.abstract_text or ''}".lower()
    disease_matches: list[Match] = []
    for disease in diseases:
        disease_id = str(disease.get("disease_id") or "")
        disease_name = str(disease.get("name_en") or "").strip().lower()
        if disease_id in _NON_CONCEPT_DISEASE_IDS or disease_name in _NON_CONCEPT_DISEASE_NAMES:
            continue
        terms = [
            str(disease.get("name_en") or ""),
            str(disease.get("name_zh") or ""),
            *[str(value) for value in disease.get("aliases") or []],
        ]
        matched = _term_matches(body_text, terms)
        if not matched:
            continue
        title_hits = _term_matches(title_text, matched)
        confidence = min(0.98, 0.62 + (0.2 if title_hits else 0.0) + 0.04 * (len(matched) - 1))
        disease_matches.append(Match(
            key=disease_id,
            label=str(disease.get("name_en") or disease["disease_id"]),
            confidence=round(confidence, 3),
            terms=matched,
        ))

    country_matches: list[Match] = []
    for country in countries:
        terms = [country.get("name", ""), country.get("name_en", ""), country.get("name_zh", "")]
        matched = _term_matches(body_text, terms)
        if matched:
            country_matches.append(Match(
                key=country["code"].upper(),
                label=country.get("name_en") or country.get("name") or country["code"],
                confidence=0.78 if _term_matches(title_text, matched) else 0.62,
                terms=matched,
            ))

    topic_matches: list[Match] = []
    for topic, terms in (taxonomy.get("topics") or {}).items():
        matched = _term_matches(body_text, [str(term) for term in terms])
        if matched:
            topic_matches.append(Match(
                key=str(topic),
                label=str(topic),
                confidence=min(0.95, 0.66 + 0.05 * (len(matched) - 1)),
                terms=matched,
            ))

    study_type = None
    for label, terms in (taxonomy.get("study_types") or {}).items():
        if _term_matches(body_text, [str(term) for term in terms]):
            study_type = str(label)
            break
    if study_type is None:
        study_type = "Journal article" if candidate.peer_review_status == "peer_reviewed" else "Preprint"

    disease_relevance = max((item.confidence for item in disease_matches), default=0.0)
    public_health_score = min(1.0, 0.25 + 0.16 * len(topic_matches)) if topic_matches else 0.15
    relevance_score = min(1.0, disease_relevance + (0.08 if topic_matches else 0.0))
    current = now or datetime.now(timezone.utc)
    age_days = max(0, (current - candidate.published_at).days) if candidate.published_at else 365
    recency = max(0.0, 1.0 - min(age_days, 365) / 365)
    study_priority = 1.0 if study_type in {"Systematic review", "Meta-analysis", "Guideline", "Outbreak investigation"} else 0.65
    oa_score = 1.0 if candidate.open_access_status == "open" else 0.0
    scored_weight = 0.35 + 0.20 + 0.15 + 0.10 + 0.05
    discovery_score = (
        0.35 * relevance_score
        + 0.20 * public_health_score
        + 0.15 * recency
        + 0.10 * study_priority
        + 0.05 * oa_score
    ) / scored_weight
    if candidate.integrity_status in {"retracted", "expression_of_concern"}:
        publication_status = "review"
    elif not disease_matches or relevance_score < 0.5:
        publication_status = "excluded"
    elif discovery_score >= auto_publish_min_score and disease_relevance >= 0.8:
        publication_status = "published"
    else:
        publication_status = "review"
    return Classification(
        diseases=sorted(disease_matches, key=lambda item: item.confidence, reverse=True),
        countries=sorted(country_matches, key=lambda item: item.confidence, reverse=True),
        topics=sorted(topic_matches, key=lambda item: item.confidence, reverse=True),
        study_type=study_type,
        relevance_score=round(relevance_score, 3),
        public_health_score=round(public_health_score, 3),
        discovery_score=round(discovery_score, 3),
        publication_status=publication_status,
    )


__all__ = ["classify_candidate"]
