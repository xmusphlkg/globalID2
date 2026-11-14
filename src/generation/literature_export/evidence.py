"""Join literature to one frozen Situation snapshot without consulting live state."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any


def _article_reference(
    article: dict[str, Any],
    relation_level: str,
    *,
    evidence_age_days: int | None = None,
    recency_status: str = "current_window",
) -> dict[str, Any]:
    return {
        "article_id": article.get("article_id"),
        "slug": article.get("slug"),
        "title": article.get("title"),
        "journal": article.get("journal"),
        "study_type": article.get("study_type"),
        "published_at": article.get("published_at"),
        "relation_level": relation_level,
        "recency_status": recency_status,
        "evidence_age_days": evidence_age_days,
        "detail_available": not bool(article.get("related_only")),
        "related_only": bool(article.get("related_only")),
        "content_tier": article.get("content_tier"),
    }


def _optional_public_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}-01" if re.fullmatch(r"\d{4}-\d{2}", text) else f"{text}-01-01")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _signal_evidence_anchor(
    item: dict[str, Any],
    snapshot: dict[str, Any],
) -> datetime | None:
    """Return the release-time anchor used for the exact-evidence recency gate."""

    candidates = (
        snapshot.get("generated_at"),
        (snapshot.get("report") or {}).get("as_of"),
        item.get("data_through"),
        item.get("published_at"),
        snapshot.get("data_through"),
    )
    return next((parsed for value in candidates if (parsed := _optional_public_datetime(value))), None)


def _signal_geographies(item: dict[str, Any]) -> list[dict[str, str]]:
    geographies: list[dict[str, str]] = []
    if item.get("country_code"):
        geography = {
            "code": str(item["country_code"]),
            "name_en": str(item.get("country_name") or item["country_code"]),
        }
        if item.get("country_name_fr"):
            geography["name_fr"] = str(item["country_name_fr"])
        geographies.append(geography)
    for raw_geography in item.get("geographies") or []:
        code = str(raw_geography.get("code") or "").strip()
        if code and all(existing["code"] != code for existing in geographies):
            geography = {
                "code": code,
                "name_en": str(raw_geography.get("name") or code),
            }
            if raw_geography.get("name_fr"):
                geography["name_fr"] = str(raw_geography["name_fr"])
            geographies.append(geography)
    return geographies


def _situation_evidence_items(
    snapshot: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Project the notice-based Situation snapshot into the literature-linking shape."""

    return [
        (section, item)
        for section in ("increasing", "emerging", "unusual")
        for item in snapshot.get(section) or []
    ]


def build_surveillance_evidence(
    articles: list[dict[str, Any]],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
    relation_decisions: list[dict[str, Any]] | None = None,
    max_exact_evidence_age_days: int = 730,
) -> dict[str, Any]:
    """Connect a Situation Room snapshot to published literature conservatively.

    Exact evidence requires disease and geography classifier confidence of at
    least 0.78 and a publication date within the configured recency window.
    Older records remain historical context and never close a current evidence
    gap. Disease-only matches are also contextual. The projection does not
    invoke a model.
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
        "exact_evidence_max_age_days": max_exact_evidence_age_days,
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
    for section, item in _situation_evidence_items(situation_snapshot):
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
            disease_name_fr = (
                disease_meta.get("name_fr")
                or disease_meta.get("standard_name_fr")
                or item.get("disease_name_fr")
            )
            exact_articles: list[dict[str, Any]] = []
            context_articles: list[dict[str, Any]] = []
            evidence_anchor = _signal_evidence_anchor(item, situation_snapshot)
            for article in articles:
                article_id = str(article.get("article_id") or "")
                decision = decisions.get((signal_id, article_id))
                if decision and decision.get("status") == "rejected":
                    continue
                article_published = _optional_public_datetime(article.get("published_at"))
                related_only = bool(article.get("related_only"))
                evidence_age_days = (
                    max(0, (evidence_anchor.date() - article_published.date()).days)
                    if evidence_anchor and article_published
                    else None
                )
                outside_exact_window = bool(
                    evidence_age_days is not None
                    and evidence_age_days > max_exact_evidence_age_days
                )
                date_unverifiable = evidence_anchor is not None and article_published is None
                if decision and decision.get("status") == "confirmed":
                    relation_level = str(decision.get("relation_level") or "disease_context")
                    requested_exact = relation_level == "exact_disease_geography"
                    is_exact = (
                        requested_exact
                        and not related_only
                        and not outside_exact_window
                        and not date_unverifiable
                    )
                    if requested_exact and outside_exact_window:
                        relation_level = "historical_disease_geography_context"
                    elif requested_exact and date_unverifiable:
                        relation_level = "disease_context"
                    elif requested_exact and related_only:
                        relation_level = "disease_context"
                else:
                    if disease_id not in article_disease_ids.get(article_id, set()):
                        continue
                    geographic_match = bool(
                        signal_country_codes
                        and signal_country_codes.intersection(article_country_codes.get(article_id, set()))
                    )
                    # A classifier-only record without a publication date cannot
                    # demonstrate that it is current enough to close a signal gap.
                    is_exact = (
                        geographic_match
                        and not related_only
                        and not outside_exact_window
                        and not date_unverifiable
                    )
                    relation_level = (
                        "exact_disease_geography"
                        if is_exact
                        else "historical_disease_geography_context"
                        if geographic_match and outside_exact_window
                        else "disease_context"
                    )
                reference = _article_reference(
                    article,
                    relation_level,
                    evidence_age_days=evidence_age_days,
                    recency_status=(
                        "outside_exact_window"
                        if outside_exact_window
                        else "date_unverifiable"
                        if evidence_anchor is not None and article_published is None
                        else "current_window"
                    ),
                )
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
                **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
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
                "historical_context_article_count": sum(
                    article.get("recency_status") == "outside_exact_window"
                    for article in context_articles
                ),
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
                    **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
                    "geographies": geographies,
                    "gap_type": "geography_coverage_gap" if context_articles else "catalogue_coverage_gap",
                    "section": section,
                    "kind": signal["kind"],
                    "data_through": signal["data_through"],
                    "risk": signal["risk"],
                    "context_article_count": len(context_articles),
                    "note_en": (
                        "Disease-level literature is available, but no recent published article has a high-confidence match to the signal geography."
                        if context_articles
                        else "No high-confidence disease-and-geography match exists in the current published Research Radar catalogue."
                    ),
                    "note_zh": (
                        "已有疾病层面的文献背景，但尚无近期已发布文献与该信号地区形成高置信度匹配。"
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
                    **({"disease_name_fr": disease_name_fr} if disease_name_fr else {}),
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
        "snapshot_id": situation_snapshot.get("snapshot_id")
        or (situation_snapshot.get("report") or {}).get("report_id"),
        "generated_at": situation_snapshot.get("generated_at")
        or (situation_snapshot.get("report") or {}).get("as_of"),
        "data_through": situation_snapshot.get("data_through")
        or (situation_snapshot.get("data_currency") or {}).get("latest_data_through"),
        "method_version": situation_snapshot.get("method_version")
        or (situation_snapshot.get("method") or {}).get("version"),
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
            "en": f"Signals come unchanged from the Situation Room snapshot. Exact links require classifier confidence of at least 0.78 for both disease and geography and publication within {max_exact_evidence_age_days} days of the signal release anchor; older or disease-only links are contextual. Gaps describe this catalogue's coverage, not the absence of research.",
            "zh": f"信号原样来自全球态势室快照。精确关联要求疾病和地区分类置信度均不低于 0.78，且论文发表于信号发布锚点前 {max_exact_evidence_age_days} 天内；更早或仅疾病匹配的文献只作为背景。缺口描述的是本目录覆盖情况，并不表示相关研究不存在。",
            "fr": f"Les signaux sont repris tels quels du snapshot de la Situation Room. Les liens exacts exigent une confiance d’au moins 0,78 pour la maladie et la géographie, ainsi qu’une publication dans les {max_exact_evidence_age_days} jours précédant l’ancrage de diffusion du signal ; les liens plus anciens ou limités à la maladie restent contextuels. Les lacunes décrivent la couverture de ce catalogue, et non l’absence de recherche.",
        },
    }


def attach_surveillance_evidence(
    payload: dict[str, Any],
    situation_snapshot: dict[str, Any] | None,
    *,
    diseases_by_id: dict[str, dict[str, Any]] | None = None,
    related_cache_path: Path | None = None,
    build_surveillance_evidence,
    attach_related_research,
    build_surveillance_coverage_matrix,
    enrich_weekly_briefs,
) -> dict[str, Any]:
    """Attach signal evidence and article backlinks without mutating input."""
    evidence_articles = [
        *(payload.get("articles") or []),
        *(payload.get("related_only_articles") or []),
    ]
    projection = build_surveillance_evidence(
        evidence_articles,
        situation_snapshot,
        diseases_by_id=diseases_by_id,
        relation_decisions=payload.get("_signal_article_links") or [],
    )
    related_by_article = projection.pop("related_by_article", {})
    projected_articles = attach_related_research([
        {
            **article,
            "related_signals": related_by_article.get(str(article.get("article_id") or ""), []),
        }
        for article in payload.get("articles") or []
    ], cache_path=related_cache_path)
    by_id = {article["article_id"]: article for article in projected_articles}
    public_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    visualizations = {
        **(public_payload.get("visualizations") or {}),
        "coverage_matrix": build_surveillance_coverage_matrix(projection),
    }
    projected_weekly_briefs = [
        {
            **brief,
            "articles": [by_id.get(article.get("article_id"), article) for article in brief.get("articles") or []],
        }
        for brief in payload.get("weekly_briefs") or []
    ]
    return {
        **public_payload,
        "articles": projected_articles,
        "related_only_articles": [
            {
                **article,
                "related_signals": related_by_article.get(str(article.get("article_id") or ""), []),
            }
            for article in payload.get("related_only_articles") or []
        ],
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
        "weekly_briefs": enrich_weekly_briefs(
            projected_weekly_briefs,
            surveillance_evidence=projection,
        ),
        "surveillance_evidence": projection,
        "visualizations": visualizations,
    }
