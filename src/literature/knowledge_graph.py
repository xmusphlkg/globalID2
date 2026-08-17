"""Deterministic public knowledge graph for Research Radar records.

The graph contains only relationships already established by the transparent
classifier or editorial metadata.  It does not use an LLM to invent entities
or edges, which keeps the public graph reproducible and auditable.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable


_ID_RE = re.compile(r"[^a-z0-9]+")

_INTERVENTION_TOPICS = {
    "vaccination": ("vaccination", "Vaccination", "疫苗接种"),
    "vaccine effectiveness": ("vaccination", "Vaccination", "疫苗接种"),
    "diagnostics": ("diagnostics", "Diagnostics", "诊断干预"),
    "treatment": ("treatment", "Treatment", "治疗干预"),
}
_POLICY_TOPICS = {
    "health policy": ("health-policy", "Health policy", "卫生政策"),
}


def _segment(value: Any) -> str:
    return _ID_RE.sub("-", str(value or "").strip().lower()).strip("-") or "unknown"


def _node_id(kind: str, value: Any) -> str:
    return f"{kind}:{_segment(value)}"


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _passes_confidence(item: dict[str, Any], minimum: float) -> bool:
    confidence = _confidence(item.get("confidence"))
    return confidence is None or confidence >= minimum


def build_knowledge_graph(
    articles: Iterable[dict[str, Any]],
    *,
    min_disease_confidence: float = 0.78,
    min_country_confidence: float = 0.78,
    min_topic_confidence: float = 0.66,
) -> dict[str, Any]:
    """Build stable nodes, edges, and co-occurrence insights from public articles."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    disease_pairs: Counter[tuple[str, str]] = Counter()
    topic_pairs: Counter[tuple[str, str]] = Counter()
    skipped_low_confidence_edges = 0

    def add_node(node_id: str, kind: str, label: str, **attributes: Any) -> None:
        nodes[node_id] = {
            "id": node_id,
            "type": kind,
            "label": label,
            **{key: value for key, value in attributes.items() if value not in (None, "", [])},
        }

    def add_edge(source: str, relation: str, target: str, **attributes: Any) -> None:
        edge_id = f"{source}|{relation}|{target}"
        edges[edge_id] = {
            "id": edge_id,
            "source": source,
            "relation": relation,
            "target": target,
            **{key: value for key, value in attributes.items() if value is not None},
        }

    for article in articles:
        article_id = _node_id("article", article.get("article_id"))
        add_node(
            article_id,
            "article",
            str(article.get("title") or article.get("article_id") or "Article"),
            slug=article.get("slug"),
            url=f"/research/articles/{article.get('slug')}/" if article.get("slug") else None,
            published_at=article.get("published_at"),
            study_type=article.get("study_type"),
        )

        disease_ids: list[str] = []
        for disease in article.get("diseases") or []:
            if not _passes_confidence(disease, min_disease_confidence):
                skipped_low_confidence_edges += 1
                continue
            raw_id = disease.get("disease_id")
            node_id = _node_id("disease", raw_id)
            disease_ids.append(node_id)
            add_node(
                node_id,
                "disease",
                str(disease.get("name_en") or raw_id),
                disease_id=raw_id,
                name_zh=disease.get("name_zh"),
                slug=disease.get("slug"),
                url=f"/diseases/{disease.get('slug')}/" if disease.get("slug") else None,
            )
            add_edge(article_id, "ABOUT_DISEASE", node_id, confidence=disease.get("confidence"))

        country_ids: list[str] = []
        for country in article.get("countries") or []:
            if not _passes_confidence(country, min_country_confidence):
                skipped_low_confidence_edges += 1
                continue
            raw_code = country.get("code")
            node_id = _node_id("country", raw_code)
            country_ids.append(node_id)
            add_node(
                node_id,
                "country",
                str(country.get("name_en") or raw_code),
                code=raw_code,
                name_zh=country.get("name_zh"),
                url=f"/countries/{str(raw_code).lower()}/" if raw_code else None,
            )
            add_edge(article_id, "STUDIED_IN", node_id, confidence=country.get("confidence"))

        topic_ids: list[str] = []
        for topic in article.get("topics") or []:
            if not _passes_confidence(topic, min_topic_confidence):
                skipped_low_confidence_edges += 1
                continue
            label = str(topic.get("name") or "Topic")
            node_id = _node_id("topic", label)
            topic_ids.append(node_id)
            add_node(node_id, "topic", label)
            add_edge(article_id, "ADDRESSES_TOPIC", node_id, confidence=topic.get("confidence"))
            normalized_label = label.strip().lower()
            if normalized_label in _INTERVENTION_TOPICS:
                value, intervention_label, intervention_label_zh = _INTERVENTION_TOPICS[normalized_label]
                intervention_id = _node_id("intervention", value)
                add_node(
                    intervention_id,
                    "intervention",
                    intervention_label,
                    name_zh=intervention_label_zh,
                    provenance="controlled_topic_mapping",
                )
                add_edge(
                    article_id,
                    "EVALUATES_INTERVENTION",
                    intervention_id,
                    confidence=topic.get("confidence"),
                    provenance="controlled_topic_mapping",
                )
            if normalized_label in _POLICY_TOPICS:
                value, policy_label, policy_label_zh = _POLICY_TOPICS[normalized_label]
                policy_id = _node_id("policy", value)
                add_node(
                    policy_id,
                    "policy",
                    policy_label,
                    name_zh=policy_label_zh,
                    provenance="controlled_topic_mapping",
                )
                add_edge(
                    article_id,
                    "INFORMS_POLICY_DOMAIN",
                    policy_id,
                    confidence=topic.get("confidence"),
                    provenance="controlled_topic_mapping",
                )

        for pathogen in article.get("pathogens") or []:
            if not isinstance(pathogen, dict) or not _passes_confidence(pathogen, min_disease_confidence):
                skipped_low_confidence_edges += 1
                continue
            label = str(pathogen.get("name") or pathogen.get("name_en") or "").strip()
            if not label:
                continue
            pathogen_id = _node_id("pathogen", pathogen.get("id") or label)
            add_node(
                pathogen_id,
                "pathogen",
                label,
                name_zh=pathogen.get("name_zh"),
                taxonomy_id=pathogen.get("id"),
            )
            add_edge(article_id, "STUDIES_PATHOGEN", pathogen_id, confidence=pathogen.get("confidence"))

        for pathogen_type in article.get("pathogen_types") or []:
            if not isinstance(pathogen_type, dict) or not _passes_confidence(pathogen_type, min_topic_confidence):
                skipped_low_confidence_edges += 1
                continue
            label = str(pathogen_type.get("name") or pathogen_type.get("label") or "").strip()
            if not label:
                continue
            pathogen_type_id = _node_id("pathogen-type", pathogen_type.get("id") or label)
            add_node(pathogen_type_id, "pathogen_type", label, provenance="controlled_classifier")
            add_edge(
                article_id,
                "HAS_PATHOGEN_TYPE",
                pathogen_type_id,
                confidence=pathogen_type.get("confidence"),
                provenance="controlled_classifier",
            )

        for population in article.get("populations") or []:
            if not isinstance(population, dict) or not _passes_confidence(population, min_topic_confidence):
                skipped_low_confidence_edges += 1
                continue
            label = str(population.get("name") or population.get("label") or "").strip()
            if not label:
                continue
            population_id = _node_id("population", population.get("id") or label)
            add_node(population_id, "population", label, provenance="controlled_classifier")
            add_edge(
                article_id,
                "STUDIES_POPULATION",
                population_id,
                confidence=population.get("confidence"),
                provenance="controlled_classifier",
            )

        summary = article.get("summary") or {}
        summary_en = summary.get("en") if isinstance(summary, dict) else None
        summary_zh = summary.get("zh") if isinstance(summary, dict) else None
        population_en = str((summary_en or {}).get("population_setting") or "").strip()
        population_zh = str((summary_zh or {}).get("population_setting") or "").strip()
        if population_en and population_zh:
            # This is a source-level, quality-gated statement rather than an
            # inferred demographic category. Keeping that distinction avoids
            # inventing population entities from prose.
            population_key = f"{article.get('article_id')}|{population_en}"
            population_id = _node_id("population-setting", population_key)
            add_node(
                population_id,
                "population_setting",
                population_en[:240],
                name_zh=population_zh[:240],
                provenance="quality_gated_bilingual_summary",
            )
            add_edge(
                article_id,
                "STUDIED_POPULATION_SETTING",
                population_id,
                provenance="quality_gated_bilingual_summary",
            )

        if article.get("study_type"):
            label = str(article["study_type"])
            study_id = _node_id("study-type", label)
            add_node(study_id, "study_type", label)
            add_edge(article_id, "USES_STUDY_DESIGN", study_id)

        for values, counter in ((disease_ids, disease_pairs), (topic_ids, topic_pairs)):
            unique = sorted(set(values))
            for index, left in enumerate(unique):
                for right in unique[index + 1 :]:
                    counter[(left, right)] += 1

    def pair_insights(counter: Counter[tuple[str, str]], kind: str) -> list[dict[str, Any]]:
        return [
            {
                "type": kind,
                "left": left,
                "right": right,
                "left_label": nodes[left]["label"],
                "right_label": nodes[right]["label"],
                "article_count": count,
            }
            for (left, right), count in counter.most_common(12)
            if count >= 2
        ]

    ordered_nodes = sorted(nodes.values(), key=lambda item: (item["type"], item["id"]))
    ordered_edges = sorted(edges.values(), key=lambda item: item["id"])
    return {
        "schema_version": 2,
        "method": "deterministic-classifier-and-quality-gated-summary-relations",
        "thresholds": {
            "disease": min_disease_confidence,
            "country": min_country_confidence,
            "topic": min_topic_confidence,
        },
        "min_relation_confidence": min(min_disease_confidence, min_country_confidence, min_topic_confidence),
        "quality": {
            "skipped_low_confidence_edges": skipped_low_confidence_edges,
        },
        "nodes": ordered_nodes,
        "edges": ordered_edges,
        "insights": [
            *pair_insights(disease_pairs, "disease_co_occurrence"),
            *pair_insights(topic_pairs, "topic_co_occurrence"),
        ],
        "stats": {
            "nodes": len(ordered_nodes),
            "edges": len(ordered_edges),
            "articles": sum(1 for item in ordered_nodes if item["type"] == "article"),
            "by_type": dict(sorted(Counter(item["type"] for item in ordered_nodes).items())),
        },
    }


__all__ = ["build_knowledge_graph"]
