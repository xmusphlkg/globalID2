"""Research Radar time windows, topic visualizations, and surveillance summaries.

Pure projection helpers live here so the literature export orchestrator can
focus on database collection and artifact publication. Existing imports from
site_data_literature remain available for callers.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from typing import Any


def _parse_public_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _facet_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


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
                disease_name_fr = disease.get("name_fr")
                for topic in topics:
                    key = (disease_id, topic)
                    disease_topic_counts[key] += 1
                    disease_topic_meta[key] = {
                        "disease_id": disease_id,
                        "disease_slug": disease.get("slug"),
                        "disease_name_en": disease_name,
                        "disease_name_zh": disease_name_zh,
                        "topic": topic,
                        **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
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
    for source, target in pairwise(quarter_periods):
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
            **({"name_fr": signal.get("disease_name_fr")} if signal.get("disease_name_fr") else {}),
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
