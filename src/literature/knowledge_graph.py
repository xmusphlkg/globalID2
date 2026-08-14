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
        "schema_version": 1,
        "method": "deterministic-classifier-relations",
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
        },
    }


__all__ = ["build_knowledge_graph"]
