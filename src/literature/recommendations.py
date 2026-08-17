"""Deterministic, auditable related-research recommendations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def _keys(items: Iterable[dict[str, Any]], key: str) -> set[str]:
    return {
        str(item.get(key) or "").strip().casefold()
        for item in items
        if str(item.get(key) or "").strip()
    }


def _date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    disease = _keys(left.get("diseases") or [], "disease_id") & _keys(right.get("diseases") or [], "disease_id")
    countries = _keys(left.get("countries") or [], "code") & _keys(right.get("countries") or [], "code")
    topics = _keys(left.get("topics") or [], "name") & _keys(right.get("topics") or [], "name")
    same_study = bool(left.get("study_type") and left.get("study_type") == right.get("study_type"))
    # Disease/topic overlap is the minimum aboutness gate. Geography or study
    # design alone must not recommend an unrelated paper.
    if not disease and not topics:
        return 0.0, [], []
    score = 4.0 * len(disease) + 2.0 * len(countries) + 1.5 * len(topics) + 0.5 * int(same_study)
    left_date, right_date = _date(left.get("published_at")), _date(right.get("published_at"))
    if left_date and right_date and abs((left_date - right_date).days) <= 730:
        score += 0.5

    reasons_en: list[str] = []
    reasons_zh: list[str] = []
    if disease:
        labels = sorted({
            str(item.get("name_en") or item.get("disease_id"))
            for item in [*(left.get("diseases") or []), *(right.get("diseases") or [])]
            if str(item.get("disease_id") or "").casefold() in disease
        })
        reasons_en.append(f"Shared disease: {', '.join(labels)}")
        reasons_zh.append(f"相同疾病：{', '.join(labels)}")
    if countries:
        reasons_en.append(f"Shared geography: {', '.join(code.upper() for code in sorted(countries))}")
        reasons_zh.append(f"相同地区：{', '.join(code.upper() for code in sorted(countries))}")
    if topics:
        labels = sorted({
            str(item.get("name"))
            for item in [*(left.get("topics") or []), *(right.get("topics") or [])]
            if str(item.get("name") or "").casefold() in topics
        })
        reasons_en.append(f"Shared topic: {', '.join(labels)}")
        reasons_zh.append(f"相同主题：{', '.join(labels)}")
    if same_study:
        reasons_en.append(f"Same study type: {left['study_type']}")
        reasons_zh.append(f"相同研究类型：{left['study_type']}")
    return score, reasons_en, reasons_zh


def attach_related_research(
    articles: Iterable[dict[str, Any]],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return article copies carrying stable related-record projections."""
    source = [dict(article) for article in articles]
    output: list[dict[str, Any]] = []
    for article in source:
        ranked: list[tuple[float, str, dict[str, Any], list[str], list[str]]] = []
        for candidate in source:
            if candidate.get("article_id") == article.get("article_id"):
                continue
            score, reasons_en, reasons_zh = _similarity(article, candidate)
            if score <= 0:
                continue
            ranked.append((
                score,
                str(candidate.get("published_at") or ""),
                candidate,
                reasons_en,
                reasons_zh,
            ))
        ranked.sort(
            key=lambda item: (item[0], item[1], str(item[2].get("article_id") or "")),
            reverse=True,
        )
        related = []
        for score, _, candidate, reasons_en, reasons_zh in ranked[: max(0, limit)]:
            related.append({
                "article_id": candidate.get("article_id"),
                "slug": candidate.get("slug"),
                "title": candidate.get("title"),
                "journal": candidate.get("journal"),
                "published_at": candidate.get("published_at"),
                "study_type": candidate.get("study_type"),
                "peer_review_status": candidate.get("peer_review_status"),
                "similarity_score": round(score, 3),
                "reasons_en": reasons_en,
                "reasons_zh": reasons_zh,
                "method": "shared_disease_geography_topic_and_study_design",
            })
        output.append({**article, "related_articles": related})
    return output


__all__ = ["attach_related_research"]
