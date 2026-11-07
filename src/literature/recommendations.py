"""Deterministic, auditable related-research recommendations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from heapq import nlargest
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


@dataclass(frozen=True, slots=True)
class _ArticleFeatures:
    diseases: set[str]
    countries: set[str]
    topics: set[str]
    study_type: Any
    published_at: datetime | None


def _features(article: dict) -> _ArticleFeatures:
    return _ArticleFeatures(
        _keys(article.get("diseases") or [], "disease_id"),
        _keys(article.get("countries") or [], "code"),
        _keys(article.get("topics") or [], "name"),
        article.get("study_type"),
        _date(article.get("published_at")),
    )


def _score(left: _ArticleFeatures, right: _ArticleFeatures) -> float:
    disease = left.diseases & right.diseases
    topics = left.topics & right.topics
    # Disease/topic overlap is the minimum aboutness gate. Geography or study
    # design alone must not recommend an unrelated paper.
    if not disease and not topics:
        return 0.0
    countries = left.countries & right.countries
    same_study = bool(left.study_type and left.study_type == right.study_type)
    score = 4.0 * len(disease) + 2.0 * len(countries) + 1.5 * len(topics) + 0.5 * int(same_study)
    left_date, right_date = left.published_at, right.published_at
    if left_date and right_date and abs((left_date - right_date).days) <= 730:
        score += 0.5
    return score


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    left_features, right_features = _features(left), _features(right)
    score = _score(left_features, right_features)
    if score <= 0:
        return 0.0, [], []
    disease = left_features.diseases & right_features.diseases
    countries = left_features.countries & right_features.countries
    topics = left_features.topics & right_features.topics
    same_study = bool(left.get("study_type") and left.get("study_type") == right.get("study_type"))

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


# These are all inputs consumed by ranking, explanations and public projections.
# Related surveillance signals and other fresh article fields are never cached.
_RELATED_INPUT_FIELDS = (
    "article_id", "slug", "title", "journal", "published_at", "study_type",
    "peer_review_status", "diseases", "countries", "topics",
)


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _cache_identity(source: list[dict], limit: int) -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(_json_bytes({
        "limit": limit,
        "articles": [{key: article.get(key) for key in _RELATED_INPUT_FIELDS} for article in source],
    }))
    return digest.hexdigest()


def _read_cached_related(path: Path, identity: str, count: int) -> list | None:
    try:
        saved = json.loads(path.read_bytes())
        rows = saved["related"]
        if (saved["identity"] == identity and isinstance(rows, list) and len(rows) == count
                and all(isinstance(row, list) and all(isinstance(item, dict) for item in row) for row in rows)
                and saved["sha256"] == hashlib.sha256(_json_bytes(rows)).hexdigest()):
            return rows
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_cached_related(path: Path, identity: str, related: list) -> None:
    body = _json_bytes({
        "identity": identity, "related": related,
        "sha256": hashlib.sha256(_json_bytes(related)).hexdigest(),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(body)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def attach_related_research(
    articles: Iterable[dict[str, Any]],
    *,
    limit: int = 6,
    cache_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return article copies carrying stable related-record projections."""
    source = [dict(article) for article in articles]
    if limit <= 0:
        return [{**article, "related_articles": []} for article in source]
    identity = _cache_identity(source, limit) if cache_path is not None else None
    if cache_path is not None:
        cached = _read_cached_related(cache_path, identity, len(source))
        if cached is not None:
            return [{**article, "related_articles": related} for article, related in zip(source, cached)]
    features = [_features(article) for article in source]
    by_disease: dict[str, set[int]] = defaultdict(set)
    by_topic: dict[str, set[int]] = defaultdict(set)
    for index, feature in enumerate(features):
        for disease in feature.diseases:
            by_disease[disease].add(index)
        for topic in feature.topics:
            by_topic[topic].add(index)
    output: list[dict[str, Any]] = []
    for article_index, article in enumerate(source):
        feature = features[article_index]
        # The aboutness gate requires a shared disease or topic. All other
        # articles have score zero, so their pairwise comparison is unnecessary.
        candidates: set[int] = set()
        for disease in feature.diseases:
            candidates.update(by_disease[disease])
        for topic in feature.topics:
            candidates.update(by_topic[topic])
        ranked: list[tuple[float, str, str, int]] = []
        for index in candidates:
            candidate = source[index]
            if candidate.get("article_id") == article.get("article_id"):
                continue
            score = _score(feature, features[index])
            if score <= 0:
                continue
            ranked.append((
                score,
                str(candidate.get("published_at") or ""),
                str(candidate.get("article_id") or ""),
                -index,
            ))
        related = []
        # Keep the original stable tie ordering without sorting all candidates;
        # explanatory strings are needed for only the final handful of papers.
        for score, _, _, negative_index in nlargest(max(0, limit), ranked):
            candidate = source[-negative_index]
            _, reasons_en, reasons_zh = _similarity(article, candidate)
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
    if cache_path is not None:
        _write_cached_related(cache_path, identity, [article["related_articles"] for article in output])
    return output


__all__ = ["attach_related_research"]
