"""Public Research Radar projection and artifact writer.

Only editorially published records are exported.  Raw abstracts and source
payloads never cross this boundary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import calendar
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureSignalArticleLink,
    LiteratureSummary,
    LiteratureTopicLink,
)
from src.generation.site_data_queries import has_table
from src.generation.site_data_writer import remove_stale_json_files, write_compact_json, write_pretty_json
from src.literature.knowledge_graph import build_knowledge_graph


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_LITERATURE_SEED_PATH = ROOT / "configs" / "literature" / "historical_seed.json"


def _parse_public_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _facet_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _normalize_seed_doi(value: Any) -> str | None:
    doi = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def _seed_identity(item: dict[str, Any]) -> tuple[str, str]:
    doi = _normalize_seed_doi(item.get("doi"))
    title = str(item.get("title") or "historical literature record")
    source = doi or f"{title.lower()}|{item.get('published_at') or 'unknown'}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    article_id = str(item.get("article_id") or f"lit_hist_{digest[:24]}")
    slug_base = _facet_slug(doi or title)[:260] or "historical-literature"
    return article_id, str(item.get("slug") or f"{slug_base}-{digest[:8]}")


def _seed_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        text = f"{text}-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text = f"{text}-01"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date_label(value: datetime | None, precision: str, *, lang: str) -> str | None:
    if value is None:
        return None
    if precision == "year":
        return f"{value.year}年" if lang == "zh" else str(value.year)
    if precision == "month":
        return value.strftime("%Y年%-m月") if lang == "zh" else value.strftime("%B %Y")
    return value.strftime("%Y年%-m月%-d日") if lang == "zh" else value.strftime("%B %-d, %Y")


def load_historical_seed_articles(seed_path: Path = HISTORICAL_LITERATURE_SEED_PATH) -> list[dict[str, Any]]:
    """Load curated historical public-literature records from a static seed."""
    if not seed_path.exists():
        return []
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    articles = payload.get("articles") if isinstance(payload, dict) else None
    return [item for item in articles or [] if isinstance(item, dict)]


def _project_historical_seed_article(
    item: dict[str, Any],
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]],
) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    published = _seed_datetime(item.get("published_at"))
    if not title or published is None:
        return None
    article_id, slug = _seed_identity(item)
    doi = _normalize_seed_doi(item.get("doi"))
    pmid = str(item.get("pmid") or "").strip() or None
    pmcid = str(item.get("pmcid") or "").strip() or None
    precision = str(item.get("publication_date_precision") or "day")
    source_urls = {
        **({"doi": f"https://doi.org/{doi}"} if doi else {}),
        **({"pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"} if pmid else {}),
        **({"pmc": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"} if pmcid else {}),
        **{str(key): str(value) for key, value in (item.get("source_urls") or {}).items() if value},
    }
    open_access_url = item.get("open_access_url") or source_urls.get("pmc")
    disease_rows: list[dict[str, Any]] = []
    for link in item.get("diseases") or []:
        disease_id = str(link.get("disease_id") or "").strip()
        if not disease_id:
            continue
        disease = diseases_by_id.get(disease_id) or {}
        disease_rows.append({
            "disease_id": disease_id,
            "slug": link.get("slug") or disease.get("slug"),
            "name_en": link.get("name_en") or disease.get("name_en") or disease.get("standard_name_en") or disease_id,
            "name_zh": link.get("name_zh") or disease.get("name_zh") or disease.get("standard_name_zh"),
            "confidence": float(link.get("confidence") or 0.94),
        })
    country_rows = [
        {
            "code": str(link.get("code") or link.get("country_code") or "").upper(),
            "name_en": str(link.get("name_en") or link.get("country_name") or link.get("code") or ""),
            "name_zh": link.get("name_zh"),
            "confidence": float(link.get("confidence") or 0.82),
        }
        for link in item.get("countries") or []
        if link.get("code") or link.get("country_code")
    ]
    topic_rows = [
        {"name": str(link.get("name") or link.get("topic")), "confidence": float(link.get("confidence") or 0.76)}
        for link in item.get("topics") or []
        if link.get("name") or link.get("topic")
    ]
    raw_summary = item.get("summary") or {}
    summary: dict[str, dict[str, Any]] = {}
    for language, values in raw_summary.items():
        if not isinstance(values, dict):
            continue
        summary[str(language)] = {
            **values,
            "provenance": {
                "generated_by": "historical-literature-seed",
                "model": None,
                "provider": "curated",
                "quality_score": 1.0,
                "generated_at": item.get("curated_at"),
                "editorially_approved": True,
                "automatically_approved": False,
                "automation_policy_version": None,
                "publication_gate": "curated-historical-baseline",
            },
        }
    authors = [str(author) for author in item.get("authors") or [] if str(author).strip()]
    study_type = str(item.get("study_type") or "Journal article")
    article_diseases = sorted(disease_rows, key=lambda row: row["confidence"], reverse=True)
    article_countries = sorted(country_rows, key=lambda row: row["confidence"], reverse=True)
    return {
        "article_id": article_id,
        "slug": slug,
        "title": title,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "journal": item.get("journal"),
        "publisher": item.get("publisher"),
        "authors": authors,
        "article_type": str(item.get("article_type") or "journal-article"),
        "study_type": study_type,
        "published_at": published.isoformat(),
        "indexed_at": _seed_datetime(item.get("indexed_at") or item.get("curated_at")).isoformat()
        if _seed_datetime(item.get("indexed_at") or item.get("curated_at"))
        else published.isoformat(),
        "publication_date_precision": precision,
        "publication_date_label_en": item.get("publication_date_label_en") or _date_label(published, precision, lang="en"),
        "publication_date_label_zh": item.get("publication_date_label_zh") or _date_label(published, precision, lang="zh"),
        "open_access_status": str(item.get("open_access_status") or ("open" if open_access_url else "unknown")),
        "open_access_url": open_access_url,
        "license_url": item.get("license_url"),
        "peer_review_status": str(item.get("peer_review_status") or "peer_reviewed"),
        "integrity_status": str(item.get("integrity_status") or "current"),
        "discovery_score": float(item.get("discovery_score") or 0.82),
        "is_featured": bool(item.get("is_featured") or False),
        "diseases": article_diseases,
        "countries": article_countries,
        "topics": sorted(topic_rows, key=lambda row: row["confidence"], reverse=True),
        "related_surveillance": build_related_surveillance(
            article_diseases,
            article_countries,
            surveillance_coverage,
        ),
        "summary": summary,
        "why_it_matters_en": item.get("why_it_matters_en")
        or (summary.get("en") or {}).get("public_health_relevance")
        or "Curated as historical baseline literature for interpreting long-running infectious-disease surveillance.",
        "why_it_matters_zh": item.get("why_it_matters_zh")
        or (summary.get("zh") or {}).get("public_health_relevance")
        or "该文献作为历史基线纳入，用于理解长期传染病监测背景。",
        "why_it_matters_source": "historical_seed",
        "source_kind": "historical_seed",
        "historical_baseline": True,
        "source_urls": {key: value for key, value in source_urls.items() if key in {"doi", "publisher", "pubmed", "pmc"}},
        "updated_at": _seed_datetime(item.get("curated_at")).isoformat()
        if _seed_datetime(item.get("curated_at"))
        else published.isoformat(),
    }


def append_historical_seed_articles(
    projected: list[dict[str, Any]],
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]] | None = None,
    seed_path: Path = HISTORICAL_LITERATURE_SEED_PATH,
) -> int:
    """Append curated historical records that are not already in the export."""
    existing_article_ids = {str(item.get("article_id") or "") for item in projected}
    existing_dois = {_normalize_seed_doi(item.get("doi")) for item in projected if item.get("doi")}
    added = 0
    for item in load_historical_seed_articles(seed_path):
        article_id, _ = _seed_identity(item)
        doi = _normalize_seed_doi(item.get("doi"))
        if article_id in existing_article_ids or (doi and doi in existing_dois):
            continue
        projected_article = _project_historical_seed_article(
            item,
            diseases_by_id=diseases_by_id,
            surveillance_coverage=surveillance_coverage or {},
        )
        if projected_article is None:
            continue
        projected.append(projected_article)
        existing_article_ids.add(projected_article["article_id"])
        if projected_article.get("doi"):
            existing_dois.add(_normalize_seed_doi(projected_article["doi"]))
        added += 1
    return added


def build_publication_timeline(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate public publication volume by month without implying causality."""
    counts = Counter(
        _parse_public_datetime(item["published_at"]).strftime("%Y-%m")
        for item in articles
        if item.get("published_at")
    )
    return [
        {"month": month, "publication_count": count}
        for month, count in sorted(counts.items())
    ]


def _iso_week(value: datetime) -> tuple[str, str]:
    iso_year, iso_week, _ = value.isocalendar()
    start = datetime.fromisocalendar(iso_year, iso_week, 1).date().isoformat()
    return f"{iso_year}-W{iso_week:02d}", start


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _month_periods(now: datetime, months: int) -> list[dict[str, str]]:
    """Return complete month buckets ending with the month containing ``now``."""
    current = now.astimezone(timezone.utc)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first = _add_months(start, -max(0, months - 1))
    rows: list[dict[str, str]] = []
    for offset in range(months):
        bucket = _add_months(first, offset)
        end = _add_months(bucket, 1) - timedelta(days=1)
        rows.append({
            "period": bucket.strftime("%Y-%m"),
            "label": bucket.strftime("%b '%y"),
            "start_date": bucket.date().isoformat(),
            "end_date": end.date().isoformat(),
        })
    return rows


def _quarter_start(value: datetime) -> datetime:
    month = ((value.month - 1) // 3) * 3 + 1
    return value.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _quarter_periods(now: datetime, quarters: int) -> list[dict[str, str]]:
    current = _quarter_start(now.astimezone(timezone.utc))
    first = _add_months(current, -3 * max(0, quarters - 1))
    rows: list[dict[str, str]] = []
    for offset in range(quarters):
        bucket = _add_months(first, offset * 3)
        end = _add_months(bucket, 3) - timedelta(days=1)
        quarter = ((bucket.month - 1) // 3) + 1
        rows.append({
            "period": f"{bucket.year}-Q{quarter}",
            "label": f"Q{quarter} '{str(bucket.year)[-2:]}",
            "start_date": bucket.date().isoformat(),
            "end_date": end.date().isoformat(),
        })
    return rows


def _month_key(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def _quarter_key(value: datetime) -> str:
    published = value.astimezone(timezone.utc)
    quarter = ((published.month - 1) // 3) + 1
    return f"{published.year}-Q{quarter}"


def _article_topics(item: dict[str, Any], *, min_confidence: float = 0.66) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for topic in item.get("topics") or []:
        name = str(topic.get("name") or "").strip()
        if not name or name in seen:
            continue
        if float(topic.get("confidence") or 0) < min_confidence:
            continue
        seen.add(name)
        rows.append(name)
    return rows


def _article_diseases(item: dict[str, Any], *, min_confidence: float = 0.78) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for disease in item.get("diseases") or []:
        disease_id = str(disease.get("disease_id") or "").strip()
        if not disease_id or disease_id in seen:
            continue
        if float(disease.get("confidence") or 0) < min_confidence:
            continue
        seen.add(disease_id)
        rows.append(disease)
    return rows


def _hotspot_article_reference(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": item.get("article_id"),
        "slug": item.get("slug"),
        "title": item.get("title"),
        "journal": item.get("journal"),
        "published_at": item.get("published_at"),
        "discovery_score": item.get("discovery_score"),
    }


def build_hotspot_visualizations(
    articles: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    months: int = 18,
    quarters: int = 6,
    top_topics: int = 8,
    heatmap_rows: int = 10,
    burst_limit: int = 12,
) -> dict[str, Any]:
    """Build compact time-based research-hotspot datasets for public charts.

    The output describes literature attention, not outbreak risk. Topic counts
    are topic mentions from quality-gated public articles; disease-topic rows
    use the same high-confidence disease threshold as public graph edges.
    """
    current = now or datetime.now(timezone.utc)
    month_periods = _month_periods(current, months)
    quarter_periods = _quarter_periods(current, quarters)
    month_keys = {period["period"] for period in month_periods}
    quarter_keys = {period["period"] for period in quarter_periods}
    monthly_article_counts: Counter[str] = Counter()
    monthly_topic_counts: dict[str, Counter[str]] = defaultdict(Counter)
    monthly_topic_articles: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    disease_topic_counts: Counter[tuple[str, str]] = Counter()
    disease_topic_meta: dict[tuple[str, str], dict[str, Any]] = {}
    monthly_disease_topic_counts: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    quarterly_topic_counts: dict[str, Counter[str]] = defaultdict(Counter)
    topic_totals: Counter[str] = Counter()

    for item in articles:
        published = _parse_public_datetime(item["published_at"]) if item.get("published_at") else None
        if not published or published > current:
            continue
        topics = _article_topics(item)
        if not topics:
            continue
        month = _month_key(published)
        quarter = _quarter_key(published)
        reference = _hotspot_article_reference(item)
        if month in month_keys:
            monthly_article_counts[month] += 1
            for topic in topics:
                monthly_topic_counts[month][topic] += 1
                monthly_topic_articles[(month, topic)].append(reference)
                topic_totals[topic] += 1
            diseases = _article_diseases(item)
            for disease in diseases:
                disease_id = str(disease.get("disease_id") or "")
                disease_name = str(disease.get("name_en") or disease_id)
                disease_name_zh = disease.get("name_zh")
                for topic in topics:
                    key = (disease_id, topic)
                    disease_topic_counts[key] += 1
                    disease_topic_meta[key] = {
                        "disease_id": disease_id,
                        "disease_slug": disease.get("slug"),
                        "disease_name_en": disease_name,
                        "disease_name_zh": disease_name_zh,
                        "topic": topic,
                    }
                    monthly_disease_topic_counts[month][key] += 1
        if quarter in quarter_keys:
            for topic in topics:
                quarterly_topic_counts[quarter][topic] += 1

    selected_topics = [name for name, _ in topic_totals.most_common(top_topics)]
    max_month_topic_mentions = max(
        (
            sum(monthly_topic_counts[period["period"]].get(topic, 0) for topic in selected_topics)
            for period in month_periods
        ),
        default=0,
    ) or 1
    stream_points_by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in selected_topics}
    for period in month_periods:
        period_key = period["period"]
        cursor = 0.0
        total_mentions = sum(monthly_topic_counts[period_key].get(topic, 0) for topic in selected_topics)
        total_height = total_mentions / max_month_topic_mentions if max_month_topic_mentions else 0
        cursor = max(0.0, (1.0 - total_height) / 2)
        article_count = monthly_article_counts.get(period_key, 0)
        for topic in selected_topics:
            count = monthly_topic_counts[period_key].get(topic, 0)
            height = count / max_month_topic_mentions
            y0 = cursor
            y1 = cursor + height
            stream_points_by_topic[topic].append({
                "period": period_key,
                "count": count,
                "share_of_articles": round(count / article_count, 4) if article_count else 0.0,
                "y0": round(y0, 4),
                "y1": round(y1, 4),
            })
            cursor = y1

    streamgraph = {
        "periods": [
            {
                **period,
                "article_count": monthly_article_counts.get(period["period"], 0),
                "topic_mentions": sum(
                    monthly_topic_counts[period["period"]].get(topic, 0)
                    for topic in selected_topics
                ),
            }
            for period in month_periods
        ],
        "series": [
            {
                "topic": topic,
                "slug": _facet_slug(topic),
                "count_total": topic_totals.get(topic, 0),
                "points": stream_points_by_topic[topic],
            }
            for topic in selected_topics
        ],
        "method": "monthly public article topic mentions, scaled by selected-topic volume",
    }

    selected_pairs = [key for key, _ in disease_topic_counts.most_common(heatmap_rows)]
    max_pair_cell_count = max(
        (
            monthly_disease_topic_counts[period["period"]].get(key, 0)
            for period in month_periods
            for key in selected_pairs
        ),
        default=0,
    ) or 1
    heatmap = {
        "periods": month_periods,
        "rows": [
            {
                **disease_topic_meta[key],
                "key": f"{key[0]}::{_facet_slug(key[1])}",
                "count_total": disease_topic_counts[key],
                "cells": [
                    {
                        "period": period["period"],
                        "count": monthly_disease_topic_counts[period["period"]].get(key, 0),
                        "intensity": round(
                            monthly_disease_topic_counts[period["period"]].get(key, 0) / max_pair_cell_count,
                            4,
                        ),
                    }
                    for period in month_periods
                ],
            }
            for key in selected_pairs
        ],
        "method": "high-confidence disease links crossed with public-health topic mentions",
    }

    bursts: list[dict[str, Any]] = []
    for topic in selected_topics:
        counts = [monthly_topic_counts[period["period"]].get(topic, 0) for period in month_periods]
        article_totals = [monthly_article_counts.get(period["period"], 0) for period in month_periods]
        for index, count in enumerate(counts):
            previous = counts[index - 1] if index else 0
            previous_window = counts[max(0, index - 3):index]
            baseline = (sum(previous_window) / len(previous_window)) if previous_window else 0.0
            share = count / article_totals[index] if article_totals[index] else 0.0
            previous_share = previous / article_totals[index - 1] if index and article_totals[index - 1] else 0.0
            growth = count - previous
            score = (count - baseline) / (max(baseline, 0.5) ** 0.5)
            if count < 2 or growth <= 0 or (score < 1.0 and share <= previous_share):
                continue
            period = month_periods[index]
            articles_for_burst = sorted(
                monthly_topic_articles.get((period["period"], topic), []),
                key=lambda article: (
                    float(article.get("discovery_score") or 0),
                    article.get("published_at") or "",
                ),
                reverse=True,
            )
            bursts.append({
                "period": period["period"],
                "label": period["label"],
                "topic": topic,
                "slug": _facet_slug(topic),
                "count": count,
                "previous_count": previous,
                "growth": growth,
                "share": round(share, 4),
                "share_delta": round(share - previous_share, 4),
                "burst_score": round(score, 2),
                "articles": articles_for_burst[:3],
            })
    bursts.sort(key=lambda item: (item["period"], item["burst_score"], item["count"]), reverse=True)

    quarter_topic_totals = Counter()
    for period in quarter_periods:
        quarter_topic_totals.update(quarterly_topic_counts[period["period"]])
    alluvial_topics = [topic for topic, _ in quarter_topic_totals.most_common(min(top_topics, 7))]
    max_quarter_mentions = max(
        (
            sum(quarterly_topic_counts[period["period"]].get(topic, 0) for topic in alluvial_topics)
            for period in quarter_periods
        ),
        default=0,
    ) or 1
    nodes: list[dict[str, Any]] = []
    node_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for period in quarter_periods:
        period_key = period["period"]
        cursor = max(
            0.0,
            (
                1.0
                - sum(quarterly_topic_counts[period_key].get(topic, 0) for topic in alluvial_topics)
                / max_quarter_mentions
            )
            / 2,
        )
        ranked_topics = sorted(
            alluvial_topics,
            key=lambda topic: (-quarterly_topic_counts[period_key].get(topic, 0), alluvial_topics.index(topic)),
        )
        for rank, topic in enumerate(ranked_topics, start=1):
            count = quarterly_topic_counts[period_key].get(topic, 0)
            height = count / max_quarter_mentions
            node = {
                "period": period_key,
                "topic": topic,
                "slug": _facet_slug(topic),
                "count": count,
                "rank": rank,
                "y0": round(cursor, 4),
                "y1": round(cursor + height, 4),
            }
            nodes.append(node)
            node_lookup[(period_key, topic)] = node
            cursor += height
    links: list[dict[str, Any]] = []
    for source, target in zip(quarter_periods, quarter_periods[1:]):
        for topic in alluvial_topics:
            source_count = quarterly_topic_counts[source["period"]].get(topic, 0)
            target_count = quarterly_topic_counts[target["period"]].get(topic, 0)
            if source_count <= 0 and target_count <= 0:
                continue
            source_node = node_lookup[(source["period"], topic)]
            target_node = node_lookup[(target["period"], topic)]
            links.append({
                "topic": topic,
                "slug": _facet_slug(topic),
                "source_period": source["period"],
                "target_period": target["period"],
                "source_count": source_count,
                "target_count": target_count,
                "value": max(source_count, target_count),
                "source_y": round((source_node["y0"] + source_node["y1"]) / 2, 4),
                "target_y": round((target_node["y0"] + target_node["y1"]) / 2, 4),
            })

    return {
        "schema_version": "research_hotspots.v1",
        "generated_at": current.isoformat(),
        "grain": {"streamgraph": "month", "heatmap": "month", "burst_timeline": "month", "alluvial": "quarter"},
        "streamgraph": streamgraph,
        "heatmap": heatmap,
        "burst_timeline": {
            "bursts": bursts[:burst_limit],
            "method": "monthly topic bursts using count growth, share movement, and recent baseline deviation",
        },
        "alluvial": {
            "periods": quarter_periods,
            "topics": [{"topic": topic, "slug": _facet_slug(topic)} for topic in alluvial_topics],
            "nodes": nodes,
            "links": links,
            "method": "quarterly topic attention flow among top public-health topics",
        },
        "interpretation_note": {
            "en": "Hotspots show Research Radar literature attention, not disease risk or incidence.",
            "zh": "热点图展示的是 Research Radar 文献关注度，不代表疾病风险或发病水平。",
        },
    }


def build_publication_pulse(
    articles: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    weeks: int = 12,
) -> list[dict[str, Any]]:
    """Weekly publication bars plus a four-week rolling average."""
    current = now or datetime.now(timezone.utc)
    week_start = datetime.fromisocalendar(current.isocalendar().year, current.isocalendar().week, 1).date()
    first_week_start = week_start - timedelta(weeks=max(0, weeks - 1))
    counts = Counter()
    for item in articles:
        if not item.get("published_at"):
            continue
        published = _parse_public_datetime(item["published_at"])
        if published > current:
            continue
        week, start = _iso_week(published)
        counts[(week, start)] += 1
    rows: list[dict[str, Any]] = []
    for index in range(weeks):
        start_date = first_week_start + timedelta(weeks=index)
        iso_year, iso_week, _ = start_date.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        count = counts.get((week, start_date.isoformat()), 0)
        rolling_values = [
            rows[position]["publication_count"]
            for position in range(max(0, len(rows) - 3), len(rows))
        ] + [count]
        rows.append({
            "week": week,
            "start_date": start_date.isoformat(),
            "publication_count": count,
            "rolling_4_week_average": round(sum(rolling_values) / len(rolling_values), 2),
        })
    return rows


def build_pipeline_funnel(
    *,
    total: int,
    review: int,
    published: int,
    excluded: int,
    summarized: int,
    exact_linked: int,
    public_catalogue: int | None = None,
) -> list[dict[str, Any]]:
    """Expose the editorial pipeline as stages with auditable counts."""
    stages = [
        ("indexed", "Indexed records", total),
        ("classified", "Classified records", total),
        ("review", "Awaiting review", review),
        ("published", "Published status", published),
        ("public_catalogue", "Public catalogue", published if public_catalogue is None else public_catalogue),
        ("excluded", "Excluded", excluded),
        ("summarized", "Published summaries", summarized),
        ("exact_linked", "Exact signal links", exact_linked),
    ]
    return [
        {
            "stage": stage,
            "label": label,
            "count": int(count or 0),
            "share_of_indexed": round((int(count or 0) / total), 4) if total else 0.0,
        }
        for stage, label, count in stages
    ]


def build_emerging_topics(
    articles: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Compare two 28-day windows using both counts and publication share."""
    current = now or datetime.now(timezone.utc)
    current_topic_counts = Counter()
    previous_topic_counts = Counter()
    current_total = 0
    previous_total = 0
    for item in articles:
        published = _parse_public_datetime(item["published_at"]) if item.get("published_at") else None
        article_topics = [topic["name"] for topic in item.get("topics") or []]
        if not published or not article_topics:
            continue
        if current - timedelta(days=28) <= published <= current:
            current_total += 1
            current_topic_counts.update(article_topics)
        elif current - timedelta(days=56) <= published < current - timedelta(days=28):
            previous_total += 1
            previous_topic_counts.update(article_topics)
    rows = []
    for name, count in current_topic_counts.items():
        previous = previous_topic_counts.get(name, 0)
        if count <= previous or count < 2:
            continue
        current_share = count / current_total if current_total else 0.0
        previous_share = previous / previous_total if previous_total else 0.0
        rows.append({
            "name": name,
            "count_28_days": count,
            "previous_28_days": previous,
            "growth": count - previous,
            "current_share": round(current_share, 4),
            "previous_share": round(previous_share, 4),
            "share_delta": round(current_share - previous_share, 4),
            "growth_ratio": round(count / previous, 2) if previous else None,
        })
    rows.sort(key=lambda item: (item["share_delta"], item["growth"], item["count_28_days"]), reverse=True)
    return rows[:8]


def build_surveillance_coverage_matrix(projection: dict[str, Any]) -> dict[str, Any]:
    """Summarize signal-literature coverage by disease and geography."""
    disease_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    disease_meta: dict[str, dict[str, Any]] = {}
    country_meta: dict[str, dict[str, Any]] = {}
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for signal in projection.get("signals") or []:
        disease_id = str(signal.get("disease_id") or "unknown")
        disease_counts[disease_id] += 1
        disease_meta[disease_id] = {
            "disease_id": disease_id,
            "name_en": signal.get("disease_name_en") or disease_id,
            "name_zh": signal.get("disease_name_zh"),
        }
        geographies = signal.get("geographies") or [{"code": "unknown", "name_en": "Geography unavailable"}]
        for geography in geographies:
            code = str(geography.get("code") or "unknown")
            country_counts[code] += 1
            country_meta[code] = {
                "code": code,
                "name_en": geography.get("name_en") or geography.get("name") or code,
            }
            key = (disease_id, code)
            cell = cells.setdefault(key, {
                "disease_id": disease_id,
                "country_code": code,
                "signals": 0,
                "exact_links": 0,
                "context_links": 0,
                "gaps": 0,
            })
            cell["signals"] += 1
            cell["exact_links"] += int(signal.get("exact_article_count") or 0)
            cell["context_links"] += int(signal.get("context_article_count") or 0)
            if signal.get("coverage_status") != "exact_evidence":
                cell["gaps"] += 1
    diseases = [disease_meta[key] for key, _ in disease_counts.most_common(8)]
    countries = [country_meta[key] for key, _ in country_counts.most_common(8)]
    allowed = {(disease["disease_id"], country["code"]) for disease in diseases for country in countries}
    return {
        "diseases": diseases,
        "countries": countries,
        "cells": [value for key, value in sorted(cells.items()) if key in allowed],
        "method": "active Situation Room signals crossed with high-confidence Research Radar links",
    }


def build_related_surveillance(
    article_diseases: list[dict[str, Any]],
    article_countries: list[dict[str, Any]],
    surveillance_coverage: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Return only precise article-place links that intersect a real GIDS series."""
    links: list[dict[str, Any]] = []
    for disease in article_diseases:
        covered_countries = surveillance_coverage.get(disease["disease_id"], set())
        for country in article_countries:
            if country["confidence"] < 0.78 or country["code"] not in covered_countries:
                continue
            links.append({
                "disease_id": disease["disease_id"],
                "disease_name_en": disease["name_en"],
                "disease_name_zh": disease.get("name_zh"),
                "disease_slug": disease.get("slug"),
                "country_code": country["code"],
                "country_name_en": country["name_en"],
                "url": f"/diseases/{disease.get('slug')}/" if disease.get("slug") else None,
                "method": "high-confidence article geography intersected with GIDS series coverage",
            })
    return links


def _article_reference(article: dict[str, Any], relation_level: str) -> dict[str, Any]:
    return {
        "article_id": article.get("article_id"),
        "slug": article.get("slug"),
        "title": article.get("title"),
        "journal": article.get("journal"),
        "study_type": article.get("study_type"),
        "published_at": article.get("published_at"),
        "relation_level": relation_level,
    }


def _signal_geographies(item: dict[str, Any]) -> list[dict[str, str]]:
    geographies: list[dict[str, str]] = []
    if item.get("country_code"):
        geographies.append({
            "code": str(item["country_code"]),
            "name_en": str(item.get("country_name") or item["country_code"]),
        })
    for geography in item.get("geographies") or []:
        code = str(geography.get("code") or "").strip()
        if code and all(existing["code"] != code for existing in geographies):
            geographies.append({
                "code": code,
                "name_en": str(geography.get("name") or code),
            })
    return geographies


def build_surveillance_evidence(
    articles: list[dict[str, Any]],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
    relation_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Connect a Situation Room snapshot to published literature conservatively.

    Exact evidence requires both disease and geography classifier confidence of
    at least 0.78. Disease-only matches remain contextual and never validate or
    explain the surveillance signal. The projection does not invoke a model.
    """
    diseases_by_id = diseases_by_id or {}
    visibility = (
        "public"
        if situation_snapshot and situation_snapshot.get("public_enabled")
        else "shadow"
        if situation_snapshot
        else "unavailable"
    )
    empty = {
        "schema_version": "research_surveillance.v1",
        "available": False,
        "visibility": visibility,
        "snapshot_id": None,
        "generated_at": None,
        "data_through": None,
        "method_version": None,
        "metrics": {
            "active_signals": 0,
            "signals_with_exact_evidence": 0,
            "exact_evidence_links": 0,
            "signals_with_disease_context": 0,
            "contextual_evidence_links": 0,
            "evidence_gaps": 0,
        },
        "signals": [],
        "evidence_gaps": [],
        "methodology": {
            "en": "No eligible Situation Room snapshot is available.",
            "zh": "当前没有可用的全球态势室快照。",
        },
    }
    if not situation_snapshot:
        return empty

    article_disease_ids: dict[str, set[str]] = {}
    article_country_codes: dict[str, set[str]] = {}
    for article in articles:
        article_id = str(article.get("article_id") or "")
        article_disease_ids[article_id] = {
            str(disease.get("disease_id"))
            for disease in article.get("diseases") or []
            if disease.get("disease_id") and float(disease.get("confidence") or 0) >= 0.78
        }
        article_country_codes[article_id] = {
            str(country.get("code") or country.get("country_code"))
            for country in article.get("countries") or []
            if (country.get("code") or country.get("country_code"))
            and float(country.get("confidence") or 0) >= 0.78
        }
    decisions = {
        (str(item.get("signal_id") or ""), str(item.get("article_id") or "")): item
        for item in relation_decisions or []
        if item.get("signal_id") and item.get("article_id")
    }

    signals: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    related_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_signal_ids: set[str] = set()
    for section in ("increasing", "emerging", "unusual"):
        for item in situation_snapshot.get(section) or []:
            signal_id = str(item.get("id") or "")
            disease_id = str(item.get("disease_id") or "")
            if not signal_id or not disease_id or signal_id in seen_signal_ids:
                continue
            seen_signal_ids.add(signal_id)
            geographies = _signal_geographies(item)
            signal_country_codes = {geography["code"] for geography in geographies}
            disease_meta = diseases_by_id.get(disease_id) or {}
            disease_name_en = str(
                disease_meta.get("name_en")
                or disease_meta.get("standard_name_en")
                or item.get("disease_name")
                or disease_id
            )
            disease_name_zh = (
                disease_meta.get("name_zh")
                or disease_meta.get("standard_name_zh")
                or disease_name_en
            )
            exact_articles: list[dict[str, Any]] = []
            context_articles: list[dict[str, Any]] = []
            for article in articles:
                article_id = str(article.get("article_id") or "")
                decision = decisions.get((signal_id, article_id))
                if decision and decision.get("status") == "rejected":
                    continue
                if decision and decision.get("status") == "confirmed":
                    relation_level = str(decision.get("relation_level") or "disease_context")
                    is_exact = relation_level == "exact_disease_geography"
                else:
                    if disease_id not in article_disease_ids.get(article_id, set()):
                        continue
                    is_exact = bool(
                        signal_country_codes
                        and signal_country_codes.intersection(article_country_codes.get(article_id, set()))
                    )
                    relation_level = "exact_disease_geography" if is_exact else "disease_context"
                reference = _article_reference(article, relation_level)
                (exact_articles if is_exact else context_articles).append(reference)
            exact_articles.sort(key=lambda article: article.get("published_at") or "", reverse=True)
            context_articles.sort(key=lambda article: article.get("published_at") or "", reverse=True)

            window = item.get("window") or {}
            risk = item.get("risk") or {}
            signal = {
                "signal_id": signal_id,
                "section": section,
                "kind": item.get("kind") or ("official_event" if section == "emerging" else "statistical_signal"),
                "title": item.get("title"),
                "disease_id": disease_id,
                "disease_name_en": disease_name_en,
                "disease_name_zh": disease_name_zh,
                "disease_slug": disease_meta.get("slug"),
                "geographies": geographies,
                "data_through": item.get("data_through") or item.get("published_at"),
                "source_label": item.get("source_label") or item.get("source"),
                "window": {
                    "label": window.get("label"),
                    "current": window.get("current"),
                    "previous": window.get("previous"),
                    "absolute_change": window.get("absolute_change"),
                    "change_pct": window.get("change_pct"),
                },
                "risk": {
                    "score": risk.get("score"),
                    "level": risk.get("level"),
                    "confidence": risk.get("confidence") or item.get("confidence"),
                },
                "signal_level": item.get("signal_level"),
                "situation_url": "/situation/",
                "visibility": visibility,
                "coverage_status": (
                    "exact_evidence"
                    if exact_articles
                    else "disease_context_only"
                    if context_articles
                    else "coverage_gap"
                ),
                "exact_article_count": len(exact_articles),
                "context_article_count": len(context_articles),
                "exact_articles": exact_articles[:4],
                "context_articles": context_articles[:4],
            }
            signals.append(signal)
            if not exact_articles:
                gaps.append({
                    "signal_id": signal_id,
                    "disease_id": disease_id,
                    "disease_name_en": disease_name_en,
                    "disease_name_zh": disease_name_zh,
                    "geographies": geographies,
                    "gap_type": "geography_coverage_gap" if context_articles else "catalogue_coverage_gap",
                    "section": section,
                    "kind": signal["kind"],
                    "data_through": signal["data_through"],
                    "risk": signal["risk"],
                    "context_article_count": len(context_articles),
                    "note_en": (
                        "Disease-level literature is available, but no published article has a high-confidence match to the signal geography."
                        if context_articles
                        else "No high-confidence disease-and-geography match exists in the current published Research Radar catalogue."
                    ),
                    "note_zh": (
                        "已有疾病层面的文献背景，但尚无已发布文献与该信号地区形成高置信度匹配。"
                        if context_articles
                        else "当前研究雷达公开目录中没有与该疾病及地区形成高置信度匹配的文献。"
                    ),
                })
            for reference in [*exact_articles, *context_articles]:
                article_id = str(reference.get("article_id") or "")
                related_by_article[article_id].append({
                    "signal_id": signal_id,
                    "section": section,
                    "kind": signal["kind"],
                    "title": signal["title"],
                    "disease_id": disease_id,
                    "disease_name_en": disease_name_en,
                    "disease_name_zh": disease_name_zh,
                    "geographies": geographies,
                    "data_through": signal["data_through"],
                    "window": signal["window"],
                    "risk": signal["risk"],
                    "relation_level": reference["relation_level"],
                    "situation_url": signal["situation_url"],
                    "visibility": visibility,
                })

    exact_links = sum(signal["exact_article_count"] for signal in signals)
    context_links = sum(signal["context_article_count"] for signal in signals)
    return {
        **empty,
        "available": True,
        "snapshot_id": situation_snapshot.get("snapshot_id"),
        "generated_at": situation_snapshot.get("generated_at"),
        "data_through": situation_snapshot.get("data_through"),
        "method_version": situation_snapshot.get("method_version"),
        "metrics": {
            "active_signals": len(signals),
            "signals_with_exact_evidence": sum(bool(signal["exact_article_count"]) for signal in signals),
            "exact_evidence_links": exact_links,
            "signals_with_disease_context": sum(bool(signal["context_article_count"]) for signal in signals),
            "contextual_evidence_links": context_links,
            "evidence_gaps": len(gaps),
        },
        "signals": signals,
        "evidence_gaps": gaps,
        "related_by_article": dict(related_by_article),
        "methodology": {
            "en": "Signals come unchanged from the Situation Room snapshot. Exact links require classifier confidence of at least 0.78 for both disease and geography; disease-only links are contextual. Gaps describe this catalogue's coverage, not the absence of research.",
            "zh": "信号原样来自全球态势室快照。精确关联要求疾病和地区分类置信度均不低于 0.78；仅疾病匹配只作为背景。缺口描述的是本目录覆盖情况，并不表示相关研究不存在。",
        },
    }


def attach_surveillance_evidence(
    payload: dict[str, Any],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach signal evidence and article backlinks without mutating input."""
    projection = build_surveillance_evidence(
        payload.get("articles") or [],
        situation_snapshot,
        diseases_by_id=diseases_by_id,
        relation_decisions=payload.get("_signal_article_links") or [],
    )
    related_by_article = projection.pop("related_by_article", {})
    projected_articles = [
        {
            **article,
            "related_signals": related_by_article.get(str(article.get("article_id") or ""), []),
        }
        for article in payload.get("articles") or []
    ]
    by_id = {article["article_id"]: article for article in projected_articles}
    public_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    visualizations = {
        **(public_payload.get("visualizations") or {}),
        "coverage_matrix": build_surveillance_coverage_matrix(projection),
    }
    return {
        **public_payload,
        "articles": projected_articles,
        "featured": [by_id.get(article.get("article_id"), article) for article in payload.get("featured") or []],
        "reviews_and_guidelines": [
            by_id.get(article.get("article_id"), article)
            for article in payload.get("reviews_and_guidelines") or []
        ],
        "disease_articles": {
            disease_id: [by_id.get(article.get("article_id"), article) for article in articles]
            for disease_id, articles in (payload.get("disease_articles") or {}).items()
        },
        "country_articles": {
            country_code: [by_id.get(article.get("article_id"), article) for article in articles]
            for country_code, articles in (payload.get("country_articles") or {}).items()
        },
        "topic_articles": {
            topic_slug: [by_id.get(article.get("article_id"), article) for article in articles]
            for topic_slug, articles in (payload.get("topic_articles") or {}).items()
        },
        "weekly_briefs": [
            {
                **brief,
                "articles": [by_id.get(article.get("article_id"), article) for article in brief.get("articles") or []],
            }
            for brief in payload.get("weekly_briefs") or []
        ],
        "surveillance_evidence": projection,
        "visualizations": visualizations,
    }


def empty_literature_export() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "last_updated": None,
        "metrics": {
            "total_public_articles": 0,
            "public_article_limit": None,
            "historical_baseline_articles": 0,
            "diseases_total": 0,
            "countries_total": 0,
            "papers_last_7_days": 0,
            "diseases_last_7_days": 0,
            "countries_last_7_days": 0,
            "reviews_and_guidelines_last_7_days": 0,
        },
        "featured": [],
        "articles": [],
        "historical_baseline": [],
        "reviews_and_guidelines": [],
        "emerging_topics": [],
        "knowledge_graph": build_knowledge_graph([]),
        "visualizations": {
            "publication_pulse": [],
            "pipeline_funnel": build_pipeline_funnel(
                total=0, review=0, published=0, excluded=0, summarized=0, exact_linked=0
            ),
            "completeness": [],
            "coverage_matrix": build_surveillance_coverage_matrix({}),
            "hotspots": build_hotspot_visualizations([]),
        },
        "disease_articles": {},
        "country_articles": {},
        "topic_articles": {},
        "facets": {"diseases": [], "countries": [], "topics": [], "weeks": []},
        "publication_timeline": [],
        "pipeline_funnel": [],
        "completeness": [],
        "weekly_briefs": [],
        "surveillance_evidence": build_surveillance_evidence([], None),
        "_signal_article_links": [],
    }


async def collect_literature_export(
    session: AsyncSession,
    *,
    diseases_by_id: dict[str, dict[str, Any]],
    surveillance_coverage: dict[str, set[str]] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    if not await has_table(session, "literature_articles"):
        return empty_literature_export()
    now = datetime.now(timezone.utc)
    status_counts = (
        await session.execute(
            select(
                func.count(LiteratureArticle.id).label("total"),
                func.count().filter(LiteratureArticle.publication_status == "review").label("review"),
                func.count().filter(LiteratureArticle.publication_status == "excluded").label("excluded"),
                func.count().filter(LiteratureArticle.publication_status == "published").label("published"),
            )
        )
    ).one()
    articles = (
        await session.execute(
            select(LiteratureArticle)
            .where(
                LiteratureArticle.publication_status == "published",
                LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                LiteratureArticle.peer_review_status == "peer_reviewed",
                LiteratureArticle.published_at.is_not(None),
                LiteratureArticle.published_at <= now,
            )
            .order_by(LiteratureArticle.is_featured.desc(), LiteratureArticle.discovery_score.desc(), LiteratureArticle.published_at.desc())
        )
    ).scalars().all()
    article_ids = [article.article_id for article in articles]
    disease_links = (
        await session.execute(select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    country_links = (
        await session.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    topic_links = (
        await session.execute(select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(article_ids)))
    ).scalars().all() if article_ids else []
    summaries = (
        await session.execute(
            select(LiteratureSummary).where(
                LiteratureSummary.article_id.in_(article_ids),
                LiteratureSummary.status == "published",
            )
        )
    ).scalars().all() if article_ids else []
    signal_article_links = (
        (
            await session.execute(
                select(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.article_id.in_(article_ids),
                    LiteratureSignalArticleLink.status.in_(("confirmed", "rejected")),
                )
            )
        ).scalars().all()
        if article_ids and await has_table(session, "literature_signal_article_links")
        else []
    )
    published_summary_article_count = int((
        await session.execute(
            select(func.count(func.distinct(LiteratureSummary.article_id))).where(
                LiteratureSummary.status == "published",
                LiteratureSummary.article_id.in_(article_ids),
            )
        )
    ).scalar_one() or 0) if article_ids else 0
    diseases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    countries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summaries_by_article: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for link in disease_links:
        disease = diseases_by_id.get(link.disease_id) or {}
        diseases[link.article_id].append({
            "disease_id": link.disease_id,
            "slug": disease.get("slug"),
            "name_en": disease.get("name_en") or disease.get("standard_name_en") or link.disease_id,
            "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
            "confidence": link.confidence,
        })
    for link in country_links:
        countries[link.article_id].append({
            "code": link.country_code,
            "name_en": link.country_name,
            "name_zh": None,
            "confidence": link.confidence,
        })
    for link in topic_links:
        topics[link.article_id].append({"name": link.topic, "confidence": link.confidence})
    summary_fields = (
        "research_question", "study_design", "population_setting", "main_findings",
        "public_health_relevance", "limitations", "gids_interpretation",
    )
    for summary in summaries:
        automation = (summary.generation_metadata or {}).get("autopilot") or {}
        summaries_by_article[summary.article_id][summary.language] = {
            **{field: getattr(summary, field) for field in summary_fields},
            "provenance": {
                "generated_by": summary.generated_by,
                "model": summary.model,
                "provider": summary.provider,
                "quality_score": summary.quality_score,
                "generated_at": summary.generated_at.isoformat() if summary.generated_at else None,
                "editorially_approved": summary.generated_by == "control-plane-editor" or bool(
                    (summary.generation_metadata or {}).get("editorial_reviewed_at")
                ),
                "automatically_approved": automation.get("policy_version") is not None,
                "automation_policy_version": automation.get("policy_version"),
                "publication_gate": (summary.generation_metadata or {}).get("publication_gate"),
            },
        }

    surveillance_coverage = surveillance_coverage or {}
    projected = []
    for article in articles:
        article_diseases = sorted(diseases[article.article_id], key=lambda item: item["confidence"], reverse=True)
        article_topics = sorted(topics[article.article_id], key=lambda item: item["confidence"], reverse=True)
        primary_disease_en = article_diseases[0]["name_en"] if article_diseases else "infectious disease"
        primary_disease_zh = article_diseases[0].get("name_zh") or "传染病"
        topic_phrase_en = ", ".join(topic["name"] for topic in article_topics[:2])
        country_phrase_en = ", ".join(country["name_en"] for country in countries[article.article_id][:2])
        study_type = article.study_type or "journal article"
        summary = summaries_by_article.get(article.article_id, {})
        summary_relevance_en = (summary.get("en") or {}).get("public_health_relevance")
        summary_relevance_zh = (summary.get("zh") or {}).get("public_health_relevance")
        context_en = (
            summary_relevance_en
            or (
                f"Classified as {study_type.lower()} evidence for {primary_disease_en}"
                f"{f' in {country_phrase_en}' if country_phrase_en else ''}"
                f"{f', with signals in {topic_phrase_en}' if topic_phrase_en else ''}. "
                "Treat this as a discovery record until the source findings are reviewed."
            )
        )
        context_zh = (
            summary_relevance_zh
            or f"该记录被分类为与{primary_disease_zh}相关的{study_type}证据。正式引用或应用结论前，请先核对原始文献。"
        )
        article_countries = sorted(countries[article.article_id], key=lambda item: item["confidence"], reverse=True)
        related_surveillance = build_related_surveillance(
            article_diseases,
            article_countries,
            surveillance_coverage,
        )
        projected.append({
            "article_id": article.article_id,
            "slug": article.slug,
            "title": article.title,
            "doi": article.doi,
            "pmid": article.pmid,
            "journal": article.journal,
            "publisher": article.publisher,
            "authors": [str(item.get("name")) for item in (article.authors or []) if item.get("name")],
            "article_type": article.article_type,
            "study_type": article.study_type,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "indexed_at": article.indexed_at.isoformat() if article.indexed_at else None,
            "open_access_status": article.open_access_status,
            "open_access_url": article.open_access_url,
            "license_url": article.license_url,
            "peer_review_status": article.peer_review_status,
            "integrity_status": article.integrity_status,
            "discovery_score": article.discovery_score,
            "is_featured": article.is_featured,
            "diseases": article_diseases,
            "countries": article_countries,
            "topics": article_topics,
            "related_surveillance": related_surveillance,
            "summary": summary,
            "why_it_matters_en": context_en,
            "why_it_matters_zh": context_zh,
            "why_it_matters_source": "published_summary" if summary_relevance_en or summary_relevance_zh else "classifier_metadata",
            "source_urls": {
                key: value
                for key, value in (article.source_urls or {}).items()
                if key in {"doi", "publisher", "pubmed"}
            },
            "updated_at": article.updated_at.isoformat() if article.updated_at else None,
        })

    historical_seed_count = append_historical_seed_articles(
        projected,
        diseases_by_id=diseases_by_id,
        surveillance_coverage=surveillance_coverage,
    )
    projected.sort(key=lambda item: int(item.get("source_kind") == "historical_seed"))
    recent_cutoff = now - timedelta(days=7)
    recent = [
        item
        for item in projected
        if item["published_at"]
        and recent_cutoff <= _parse_public_datetime(item["published_at"]) <= now
    ]
    review_types = {"Systematic review", "Meta-analysis", "Guideline"}
    emerging_topics = build_emerging_topics(projected, now=now)
    disease_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    country_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    topic_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    weekly_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disease_meta: dict[str, dict[str, Any]] = {}
    country_meta: dict[str, dict[str, Any]] = {}
    topic_meta: dict[str, dict[str, Any]] = {}
    for item in projected:
        for disease in item["diseases"]:
            key = disease["disease_id"].lower()
            disease_articles[key].append(item)
            disease_meta[key] = {
                "disease_id": disease["disease_id"],
                "slug": disease.get("slug") or key,
                "name_en": disease["name_en"],
                "name_zh": disease.get("name_zh"),
            }
        for country in item["countries"]:
            key = country["code"].lower()
            country_articles[key].append(item)
            country_meta[key] = {
                "code": country["code"],
                "slug": key,
                "name_en": country["name_en"],
                "name_zh": country.get("name_zh"),
            }
        for topic in item["topics"]:
            key = _facet_slug(topic["name"])
            if not key:
                continue
            topic_articles[key].append(item)
            topic_meta[key] = {"slug": key, "name": topic["name"]}
        if item.get("published_at") and _parse_public_datetime(item["published_at"]) <= now:
            published = _parse_public_datetime(item["published_at"])
            iso_year, iso_week, _ = published.isocalendar()
            weekly_articles[f"{iso_year}-W{iso_week:02d}"].append(item)
    disease_facets = [
        {**disease_meta[key], "count": len(items), "url": f"/research/diseases/{disease_meta[key]['slug']}/"}
        for key, items in sorted(disease_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    country_facets = [
        {**country_meta[key], "count": len(items), "url": f"/research/countries/{key}/"}
        for key, items in sorted(country_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    topic_facets = [
        {**topic_meta[key], "count": len(items), "url": f"/research/topics/{key}/"}
        for key, items in sorted(topic_articles.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]
    weekly_briefs = []
    for week, items in sorted(weekly_articles.items(), reverse=True):
        published_dates = sorted(_parse_public_datetime(item["published_at"]) for item in items)
        weekly_briefs.append({
            "week": week,
            "start_date": published_dates[0].date().isoformat(),
            "end_date": published_dates[-1].date().isoformat(),
            "article_count": len(items),
            "disease_count": len({d["disease_id"] for item in items for d in item["diseases"]}),
            "country_count": len({c["code"] for item in items for c in item["countries"]}),
            "top_topics": [name for name, _ in Counter(t["name"] for item in items for t in item["topics"]).most_common(5)],
            "articles": items,
            "url": f"/research/weekly/{week}/",
        })
    latest_updated_values = [
        article.updated_at
        for article in articles
        if article.updated_at
    ]
    latest_updated_values.extend(
        _parse_public_datetime(item["updated_at"])
        for item in projected
        if item.get("source_kind") == "historical_seed" and item.get("updated_at")
    )
    latest_updated = max(latest_updated_values, default=None)
    exact_linked_public_articles = len({
        link.article_id
        for link in signal_article_links
        if link.status == "confirmed" and link.relation_level == "exact_disease_geography"
    })
    pipeline_funnel = build_pipeline_funnel(
        total=int(status_counts.total or 0),
        review=int(status_counts.review or 0),
        published=int(status_counts.published or 0),
        public_catalogue=len(projected),
        excluded=int(status_counts.excluded or 0),
        summarized=published_summary_article_count,
        exact_linked=exact_linked_public_articles,
    )
    completeness = [
        {"metric": "Disease classified", "count": sum(bool(item["diseases"]) for item in projected), "total": len(projected)},
        {"metric": "Geography classified", "count": sum(bool(item["countries"]) for item in projected), "total": len(projected)},
        {"metric": "Topic classified", "count": sum(bool(item["topics"]) for item in projected), "total": len(projected)},
        {"metric": "Published summary", "count": published_summary_article_count, "total": len(projected)},
        {"metric": "Open access", "count": sum(item["open_access_status"] == "open" for item in projected), "total": len(projected)},
    ]
    return {
        "schema_version": 1,
        "last_updated": latest_updated.isoformat() if latest_updated else None,
        "metrics": {
            "total_public_articles": len(projected),
            "public_article_limit": None,
            "historical_baseline_articles": historical_seed_count,
            "diseases_total": len({d["disease_id"] for item in projected for d in item["diseases"]}),
            "countries_total": len({c["code"] for item in projected for c in item["countries"]}),
            "papers_last_7_days": len(recent),
            "diseases_last_7_days": len({d["disease_id"] for item in recent for d in item["diseases"]}),
            "countries_last_7_days": len({c["code"] for item in recent for c in item["countries"]}),
            "reviews_and_guidelines_last_7_days": sum(item["study_type"] in review_types for item in recent),
        },
        "featured": [item for item in projected if item["is_featured"]][:6],
        "articles": projected,
        "historical_baseline": sorted(
            [item for item in projected if item.get("source_kind") == "historical_seed"],
            key=lambda item: item.get("published_at") or "",
        ),
        "reviews_and_guidelines": [item for item in projected if item["study_type"] in review_types][:12],
        "emerging_topics": emerging_topics,
        "knowledge_graph": build_knowledge_graph(projected),
        "disease_articles": dict(disease_articles),
        "country_articles": dict(country_articles),
        "topic_articles": dict(topic_articles),
        "facets": {
            "diseases": disease_facets,
            "countries": country_facets,
            "topics": topic_facets,
            "weeks": [
                {key: value for key, value in brief.items() if key != "articles"}
                for brief in weekly_briefs
            ],
        },
        "publication_timeline": build_publication_timeline(projected),
        "pipeline_funnel": pipeline_funnel,
        "completeness": completeness,
        "visualizations": {
            "publication_pulse": build_publication_pulse(projected, now=now),
            "pipeline_funnel": pipeline_funnel,
            "completeness": completeness,
            "coverage_matrix": build_surveillance_coverage_matrix({}),
            "hotspots": build_hotspot_visualizations(projected, now=now),
        },
        "weekly_briefs": weekly_briefs,
        "_signal_article_links": [
            {
                "signal_id": link.signal_id,
                "article_id": link.article_id,
                "relation_level": link.relation_level,
                "status": link.status,
                "confidence": link.confidence,
                "source": link.source,
            }
            for link in signal_article_links
        ],
    }


def write_literature_artifacts(payload: dict[str, Any], output_dir: Path) -> None:
    research_dir = output_dir / "research"
    article_dir = research_dir / "articles"
    disease_dir = research_dir / "diseases"
    country_dir = research_dir / "countries"
    topic_dir = research_dir / "topics"
    weekly_dir = research_dir / "weekly"
    article_dir.mkdir(parents=True, exist_ok=True)
    disease_dir.mkdir(parents=True, exist_ok=True)
    country_dir.mkdir(parents=True, exist_ok=True)
    topic_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)
    public_index = {
        key: value
        for key, value in payload.items()
        if key not in {"disease_articles", "country_articles", "topic_articles"} and not key.startswith("_")
    }
    public_index["weekly_briefs"] = [
        {key: value for key, value in brief.items() if key != "articles"}
        for brief in payload.get("weekly_briefs") or []
    ]
    write_pretty_json(research_dir / "index.json", public_index)
    hotspots = ((payload.get("visualizations") or {}).get("hotspots") or build_hotspot_visualizations(
        payload.get("articles") or []
    ))
    write_pretty_json(research_dir / "hotspots.json", hotspots)
    write_compact_json(
        research_dir / "catalogue.json",
        {
            "schema_version": public_index.get("schema_version"),
            "last_updated": public_index.get("last_updated"),
            "articles": payload.get("articles") or [],
        },
    )
    for article in payload.get("articles") or []:
        write_pretty_json(
            article_dir / f"{article['slug']}.json",
            {**article, "knowledge_graph": build_knowledge_graph([article])},
        )
    for disease_id, articles in (payload.get("disease_articles") or {}).items():
        write_pretty_json(
            disease_dir / f"{disease_id}.json",
            {
                "articles": articles,
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    for country_code, articles in (payload.get("country_articles") or {}).items():
        write_pretty_json(
            country_dir / f"{country_code}.json",
            {
                "articles": articles,
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    for topic_slug, articles in (payload.get("topic_articles") or {}).items():
        write_pretty_json(
            topic_dir / f"{topic_slug}.json",
            {
                "articles": articles,
                "count": len(articles),
                "publication_timeline": build_publication_timeline(articles),
                "knowledge_graph": build_knowledge_graph(articles),
            },
        )
    for brief in payload.get("weekly_briefs") or []:
        write_pretty_json(weekly_dir / f"{brief['week']}.json", brief)
    remove_stale_json_files(article_dir, {f"{item['slug']}.json" for item in payload.get("articles") or []})
    remove_stale_json_files(disease_dir, {f"{key}.json" for key in (payload.get("disease_articles") or {})})
    remove_stale_json_files(country_dir, {f"{key}.json" for key in (payload.get("country_articles") or {})})
    remove_stale_json_files(topic_dir, {f"{key}.json" for key in (payload.get("topic_articles") or {})})
    remove_stale_json_files(weekly_dir, {f"{item['week']}.json" for item in payload.get("weekly_briefs") or []})


__all__ = [
    "attach_surveillance_evidence",
    "build_emerging_topics",
    "build_hotspot_visualizations",
    "build_pipeline_funnel",
    "build_publication_pulse",
    "build_related_surveillance",
    "build_publication_timeline",
    "build_surveillance_coverage_matrix",
    "build_surveillance_evidence",
    "collect_literature_export",
    "empty_literature_export",
    "append_historical_seed_articles",
    "load_historical_seed_articles",
    "write_literature_artifacts",
]
