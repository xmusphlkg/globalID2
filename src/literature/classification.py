"""Auditable two-stage classification for Research Radar records.

Stage one uses conservative lexical matching over title and abstract text.
Stage two can add evidence from provider-controlled semantic metadata (Europe
PMC MeSH/keywords/publication types and OpenAlex topics/keywords/concepts).
Every match term includes its stage and source so downstream review can explain
why a relationship was created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable

import pycountry

from .types import ArticleCandidate, Classification, Match


CLASSIFICATION_VERSION = 5
_NON_CONCEPT_DISEASE_IDS = {"D999"}
_NON_CONCEPT_DISEASE_NAMES = {"all", "other", "total", "unknown", "unspecified"}
_AMBIGUOUS_COUNTRY_TERMS = {"congo", "korea"}
_SPACE_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_SEMANTIC_MIN_SCORES = {
    "europe_pmc.mesh": 0.0,
    "europe_pmc.keyword": 0.0,
    "europe_pmc.pub_type": 0.0,
    "openalex.topic": 0.35,
    "openalex.keyword": 0.45,
    # OpenAlex documents Concepts as higher-recall/lower-precision than Topics.
    "openalex.concept": 0.65,
}
_SEMANTIC_CONFIDENCE_BASE = {
    "europe_pmc.mesh": 0.78,
    "europe_pmc.keyword": 0.70,
    "europe_pmc.pub_type": 0.74,
    "openalex.topic": 0.72,
    "openalex.keyword": 0.69,
    "openalex.concept": 0.60,
}
_DISCOVERY_WEIGHTS = {
    "relevance": 0.35,
    "public_health": 0.20,
    "recency": 0.15,
    "study_priority": 0.10,
    "open_access": 0.05,
    "surveillance_relation": 0.15,
}
_SURVEILLANCE_RELATION_SCORES = {
    "exact_disease_geography": 1.0,
    "disease_context": 0.6,
    "candidate": 0.25,
}


@dataclass(frozen=True, slots=True)
class _SemanticTerm:
    value: str
    source: str
    score: float


@dataclass(frozen=True, slots=True)
class _EvidenceHit:
    term: str
    trace: str
    confidence: float
    stage: str


def _compact(value: Any, *, limit: int = 180) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _is_short_acronym(term: str) -> bool:
    letters = re.sub(r"[^A-Za-z0-9]", "", term)
    return 2 <= len(letters) <= 3 and letters.isupper()


def _contains(text: str, term: str) -> bool:
    """Match a term at token boundaries without turning ``us`` into ``US``.

    Two-to-three character acronyms are deliberately case-sensitive. CJK terms
    can be two characters; other non-acronym terms must contain at least three
    characters to avoid noisy substring matches.
    """

    normalized = _compact(term)
    if not normalized:
        return False
    if _is_short_acronym(normalized):
        return re.search(
            rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])",
            text,
        ) is not None
    if (
        len(normalized) < 3
        and not _is_short_acronym(normalized)
        and not (_CJK_RE.search(normalized) and len(normalized) >= 2)
    ):
        return False
    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    ) is not None


def _term_spans(text: str, term: str) -> list[tuple[int, int]]:
    normalized = _compact(term)
    if not normalized:
        return []
    flags = 0 if _is_short_acronym(normalized) else re.IGNORECASE
    if (
        len(normalized) < 3
        and not _is_short_acronym(normalized)
        and not (_CJK_RE.search(normalized) and len(normalized) >= 2)
    ):
        return []
    return [
        match.span()
        for match in re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])",
            text,
            flags,
        )
    ]


def _term_matches(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in dict.fromkeys(_compact(term) for term in terms) if term and _contains(text, term)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _named_value(value: Any) -> str:
    if isinstance(value, dict):
        return _compact(value.get("display_name") or value.get("name") or value.get("$"))
    return _compact(value)


def _score(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def surveillance_relation_score_for_level(relation_level: str | None) -> float:
    """Map a reviewable surveillance-link level to its normalized score."""

    return _SURVEILLANCE_RELATION_SCORES.get(str(relation_level or ""), 0.0)


def _discovery_components(**values: float) -> dict[str, dict[str, float]]:
    return {
        name: {
            "value": round(_score(values.get(name), 0.0), 3),
            "weight": weight,
            "contribution": round(_score(values.get(name), 0.0) * weight, 4),
        }
        for name, weight in _DISCOVERY_WEIGHTS.items()
    }


def _total_discovery_score(components: dict[str, dict[str, float]]) -> float:
    return round(sum(item["contribution"] for item in components.values()), 3)


def apply_surveillance_relation(
    classification: Classification,
    relation_level: str | None,
) -> Classification:
    """Fill the 15% surveillance component after a gap link is classified.

    Gap discovery cannot know the relationship level until disease and country
    classification has run. This small second pass preserves those results and
    changes only the traceable score component and total.
    """

    relation_score = surveillance_relation_score_for_level(relation_level)
    components = {
        name: dict(item)
        for name, item in classification.discovery_score_components.items()
    }
    components["surveillance_relation"] = {
        "value": relation_score,
        "weight": _DISCOVERY_WEIGHTS["surveillance_relation"],
        "contribution": round(
            relation_score * _DISCOVERY_WEIGHTS["surveillance_relation"],
            4,
        ),
    }
    classification.surveillance_relation_level = relation_level
    classification.surveillance_relation_score = relation_score
    classification.discovery_score_components = components
    classification.discovery_score = _total_discovery_score(components)
    return classification


def _semantic_terms(candidate: ArticleCandidate) -> list[_SemanticTerm]:
    """Extract only documented, provider-controlled subject metadata."""

    output: list[_SemanticTerm] = []
    europe_pmc = candidate.source_payload.get("europe_pmc")
    if isinstance(europe_pmc, dict):
        headings = (europe_pmc.get("meshHeadingList") or {}).get("meshHeading") or []
        for heading in _as_list(headings):
            if isinstance(heading, dict):
                value = _named_value(heading.get("descriptorName"))
                major = str(heading.get("majorTopic_YN") or "").upper() == "Y"
            else:
                value = _named_value(heading)
                major = False
            if value:
                output.append(_SemanticTerm(value, "europe_pmc.mesh", 1.0 if major else 0.88))

        keywords = (europe_pmc.get("keywordList") or {}).get("keyword") or []
        for keyword in _as_list(keywords):
            value = _named_value(keyword)
            if value:
                output.append(_SemanticTerm(value, "europe_pmc.keyword", 0.80))

        publication_types = (europe_pmc.get("pubTypeList") or {}).get("pubType") or []
        for publication_type in _as_list(publication_types):
            value = _named_value(publication_type)
            if value:
                output.append(_SemanticTerm(value, "europe_pmc.pub_type", 0.90))

    openalex = candidate.source_payload.get("openalex")
    if isinstance(openalex, dict):
        topic_rows = [*_as_list(openalex.get("topics"))]
        primary_topic = openalex.get("primary_topic")
        if primary_topic:
            topic_rows.append(primary_topic)
        for topic in topic_rows:
            if not isinstance(topic, dict):
                continue
            value = _named_value(topic.get("display_name"))
            if value:
                output.append(_SemanticTerm(value, "openalex.topic", _score(topic.get("score"), 0.70)))
        for keyword in _as_list(openalex.get("keywords")):
            if not isinstance(keyword, dict):
                continue
            value = _named_value(keyword.get("display_name"))
            if value:
                output.append(_SemanticTerm(value, "openalex.keyword", _score(keyword.get("score"), 0.55)))
        for concept in _as_list(openalex.get("concepts")):
            if not isinstance(concept, dict):
                continue
            value = _named_value(concept.get("display_name"))
            if value:
                output.append(_SemanticTerm(value, "openalex.concept", _score(concept.get("score"), 0.50)))

    deduplicated: dict[tuple[str, str], _SemanticTerm] = {}
    for item in output:
        key = (item.source, item.value.casefold())
        if key not in deduplicated or item.score > deduplicated[key].score:
            deduplicated[key] = item
    return list(deduplicated.values())


def _lexical_hits(title: str, body: str, terms: Iterable[str]) -> list[_EvidenceHit]:
    matched = _term_matches(body, terms)
    title_hits = set(_term_matches(title, matched))
    return [
        _EvidenceHit(
            term=term,
            trace=f"lexical:{'title' if term in title_hits else 'abstract'}:{term}",
            confidence=0.82 if term in title_hits else 0.62,
            stage="lexical",
        )
        for term in matched
    ]


def _semantic_hits(
    semantic_terms: list[_SemanticTerm],
    terms: Iterable[str],
    *,
    exact: bool = False,
) -> list[_EvidenceHit]:
    hits: list[_EvidenceHit] = []
    normalized_terms = list(dict.fromkeys(_compact(term) for term in terms if _compact(term)))
    for metadata_term in semantic_terms:
        minimum = _SEMANTIC_MIN_SCORES.get(metadata_term.source, 1.0)
        if metadata_term.score < minimum:
            continue
        matched = (
            [
                term
                for term in normalized_terms
                if (
                    metadata_term.value == term
                    if _is_short_acronym(term)
                    else metadata_term.value.casefold() == term.casefold()
                )
            ]
            if exact
            else _term_matches(metadata_term.value, normalized_terms)
        )
        for term in matched:
            base = _SEMANTIC_CONFIDENCE_BASE[metadata_term.source]
            confidence = min(0.96, base + 0.16 * metadata_term.score)
            hits.append(_EvidenceHit(
                term=term,
                trace=(
                    f"semantic:{metadata_term.source}[{metadata_term.score:.3f}]:"
                    f"{metadata_term.value}=>{term}"
                ),
                confidence=confidence,
                stage="semantic",
            ))
    return hits


def _match_confidence(hits: list[_EvidenceHit], *, maximum: float = 0.98) -> float:
    if not hits:
        return 0.0
    confidence = max(hit.confidence for hit in hits)
    if {hit.stage for hit in hits} == {"lexical", "semantic"}:
        confidence += 0.04
    source_count = len({hit.trace.split(":", 2)[1] for hit in hits})
    if source_count > 1:
        confidence += min(0.04, 0.01 * (source_count - 1))
    return round(min(maximum, confidence), 3)


def _country_terms(country: dict[str, Any], taxonomy: dict[str, Any]) -> list[str]:
    code = str(country.get("code") or "").upper()
    terms = [
        country.get("name", ""),
        country.get("name_en", ""),
        country.get("name_zh", ""),
        *[str(value) for value in country.get("aliases") or []],
        *[str(value) for value in (taxonomy.get("country_aliases") or {}).get(code, [])],
    ]
    iso_country = pycountry.countries.get(alpha_2=code) if len(code) == 2 else None
    if iso_country is not None:
        terms.extend([
            getattr(iso_country, "name", ""),
            getattr(iso_country, "official_name", ""),
            getattr(iso_country, "common_name", ""),
            getattr(iso_country, "alpha_3", ""),
        ])
    return [
        term
        for term in dict.fromkeys(_compact(value) for value in terms)
        if term and term.casefold() not in _AMBIGUOUS_COUNTRY_TERMS
    ]


def _drop_shadowed_country_matches(matches: list[Match], body_text: str) -> list[Match]:
    """Drop a shorter geography alias embedded in a more specific country hit."""

    keep: list[Match] = []
    for match in matches:
        lexical_terms = [
            trace.rsplit(":", 1)[-1]
            for trace in match.terms
            if trace.startswith("lexical:")
        ]
        lexical_spans = [
            (term, span)
            for term in lexical_terms
            for span in _term_spans(body_text, term)
        ]
        longer_spans = [
            (other_term, span)
            for other in matches
            if other.key != match.key
            for trace in other.terms
            if trace.startswith("lexical:")
            for other_term in [trace.rsplit(":", 1)[-1]]
            for span in _term_spans(body_text, other_term)
        ]
        has_unshadowed_lexical_hit = any(
            not any(
                term.casefold() != other_term.casefold()
                and term.casefold() in other_term.casefold()
                and other_start <= start
                and end <= other_end
                for other_term, (other_start, other_end) in longer_spans
            )
            for term, (start, end) in lexical_spans
        )
        has_semantic_hit = any(trace.startswith("semantic:") for trace in match.terms)
        if has_unshadowed_lexical_hit or has_semantic_hit:
            keep.append(match)
    return keep


def _controlled_entity_matches(
    candidate: ArticleCandidate,
    semantic_terms: list[_SemanticTerm],
    taxonomy_section: dict[str, Any],
) -> list[Match]:
    title_text = candidate.title
    body_text = f"{candidate.title} {candidate.abstract_text or ''}"
    matches: list[Match] = []
    for key, raw in taxonomy_section.items():
        spec = raw if isinstance(raw, dict) else {"aliases": raw}
        label = str(spec.get("name") or spec.get("label") or key)
        terms = [label, *[str(value) for value in spec.get("aliases") or []]]
        hits = [
            *_lexical_hits(title_text, body_text, terms),
            *_semantic_hits(semantic_terms, terms),
        ]
        if hits:
            matches.append(Match(
                key=str(key),
                label=label,
                confidence=_match_confidence(hits, maximum=0.96),
                terms=list(dict.fromkeys(hit.trace for hit in hits)),
            ))
    return sorted(matches, key=lambda item: item.confidence, reverse=True)


def classify_candidate(
    candidate: ArticleCandidate,
    *,
    diseases: list[dict[str, Any]],
    countries: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    now: datetime | None = None,
    auto_publish_min_score: float = 0.86,
    surveillance_relation_score: float = 0.0,
) -> Classification:
    title_text = candidate.title
    body_text = f"{candidate.title} {candidate.abstract_text or ''}"
    semantic_terms = _semantic_terms(candidate)

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
        hits = [
            *_lexical_hits(title_text, body_text, terms),
            *_semantic_hits(semantic_terms, terms),
        ]
        if not hits:
            continue
        disease_matches.append(Match(
            key=disease_id,
            label=str(disease.get("name_en") or disease["disease_id"]),
            confidence=_match_confidence(hits),
            terms=list(dict.fromkeys(hit.trace for hit in hits)),
        ))

    country_matches: list[Match] = []
    for country in countries:
        terms = _country_terms(country, taxonomy)
        hits = [
            *_lexical_hits(title_text, body_text, terms),
            *_semantic_hits(semantic_terms, terms, exact=True),
        ]
        if not hits:
            continue
        # Geography requires a little more caution than disease aboutness.
        confidence = _match_confidence(hits, maximum=0.94)
        if any(hit.stage == "lexical" and hit.trace.startswith("lexical:title:") for hit in hits):
            confidence = max(confidence, 0.82)
        country_matches.append(Match(
            key=str(country["code"]).upper(),
            label=str(country.get("name_en") or country.get("name") or country["code"]),
            confidence=round(confidence, 3),
            terms=list(dict.fromkeys(hit.trace for hit in hits)),
        ))
    country_matches = _drop_shadowed_country_matches(country_matches, body_text)

    topic_matches: list[Match] = []
    for topic, terms in (taxonomy.get("topics") or {}).items():
        normalized_terms = [str(term) for term in terms]
        hits = [
            *_lexical_hits(title_text, body_text, normalized_terms),
            *_semantic_hits(semantic_terms, normalized_terms),
        ]
        if hits:
            topic_matches.append(Match(
                key=str(topic),
                label=str(topic),
                confidence=max(0.66, _match_confidence(hits, maximum=0.95)),
                terms=list(dict.fromkeys(hit.trace for hit in hits)),
            ))

    pathogen_matches = _controlled_entity_matches(
        candidate,
        semantic_terms,
        taxonomy.get("pathogens") or {},
    )
    pathogen_type_matches = _controlled_entity_matches(
        candidate,
        semantic_terms,
        taxonomy.get("pathogen_types") or {},
    )
    inferred_pathogen_types = {
        str((taxonomy.get("pathogens") or {}).get(match.key, {}).get("type") or "")
        for match in pathogen_matches
    }
    for pathogen_type in inferred_pathogen_types:
        if not pathogen_type or any(match.key == pathogen_type for match in pathogen_type_matches):
            continue
        spec = (taxonomy.get("pathogen_types") or {}).get(pathogen_type) or {}
        pathogen_type_matches.append(Match(
            key=pathogen_type,
            label=str(spec.get("label") or pathogen_type),
            confidence=round(max(match.confidence for match in pathogen_matches), 3),
            terms=[f"inferred:controlled_pathogen:{match.key}" for match in pathogen_matches],
        ))
    pathogen_type_matches.sort(key=lambda item: item.confidence, reverse=True)
    population_matches = _controlled_entity_matches(
        candidate,
        semantic_terms,
        taxonomy.get("populations") or {},
    )

    domain_hits: dict[str, list[_EvidenceHit]] = {}
    for domain, terms in (taxonomy.get("research_domains") or {}).items():
        normalized_terms = [str(term) for term in terms]
        domain_hits[str(domain)] = [
            *_lexical_hits(title_text, body_text, normalized_terms),
            *_semantic_hits(semantic_terms, normalized_terms),
        ]
    human_hit = bool(domain_hits.get("human_health"))
    animal_hit = bool(domain_hits.get("animal"))
    plant_hit = bool(domain_hits.get("plant"))
    basic_hit = bool(domain_hits.get("basic_research"))
    one_health_hit = bool(domain_hits.get("one_health")) or any(
        match.key == "One Health" for match in topic_matches
    )
    public_health_context = bool(topic_matches) and bool(disease_matches)
    if one_health_hit or (human_hit and animal_hit):
        research_domain = "one_health"
    elif human_hit or public_health_context:
        research_domain = "human_health"
    elif plant_hit:
        research_domain = "plant_only"
    elif animal_hit:
        research_domain = "animal_only"
    elif basic_hit:
        research_domain = "basic_research"
    else:
        research_domain = "not_determined"
    research_domain_terms = list(dict.fromkeys(
        hit.trace for hits in domain_hits.values() for hit in hits
    ))

    study_type = None
    for label, terms in (taxonomy.get("study_types") or {}).items():
        normalized_terms = [str(term) for term in terms]
        if _lexical_hits(title_text, body_text, normalized_terms) or _semantic_hits(semantic_terms, normalized_terms):
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
    priority_study_types = {
        "Systematic review",
        "Meta-analysis",
        "Guideline",
        "Outbreak investigation",
    }
    study_priority = 1.0 if study_type in priority_study_types else 0.65
    oa_score = 1.0 if candidate.open_access_status == "open" else 0.0
    relation_score = _score(surveillance_relation_score, 0.0)
    discovery_score_components = _discovery_components(
        relevance=relevance_score,
        public_health=public_health_score,
        recency=recency,
        study_priority=study_priority,
        open_access=oa_score,
        surveillance_relation=relation_score,
    )
    discovery_score = _total_discovery_score(discovery_score_components)
    if candidate.integrity_status in {"retracted", "expression_of_concern"}:
        publication_status = "review"
    elif research_domain == "plant_only":
        publication_status = "excluded"
    elif research_domain in {"animal_only", "basic_research"}:
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
        pathogens=pathogen_matches,
        pathogen_types=pathogen_type_matches,
        populations=population_matches,
        research_domain=research_domain,
        research_domain_terms=research_domain_terms,
        study_type=study_type,
        relevance_score=round(relevance_score, 3),
        public_health_score=round(public_health_score, 3),
        surveillance_relation_level=None,
        surveillance_relation_score=relation_score,
        discovery_score_components=discovery_score_components,
        discovery_score=round(discovery_score, 3),
        publication_status=publication_status,
    )


__all__ = [
    "CLASSIFICATION_VERSION",
    "apply_surveillance_relation",
    "classify_candidate",
    "surveillance_relation_score_for_level",
]
