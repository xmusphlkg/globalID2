"""Safe, repeatable backfill for versioned Research Radar classification."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.core.config import get_config
from src.core.database import get_db
from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureEvidenceGap,
    LiteratureSignalArticleLink,
    LiteratureTopicLink,
)

from .classification import (
    CLASSIFICATION_VERSION,
    classify_candidate,
    surveillance_relation_score_for_level,
)
from .pipeline import LiteraturePipeline, ROOT, _load_json
from .repository import LiteratureRepository
from .types import ArticleCandidate


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def candidate_from_stored_article(article: LiteratureArticle) -> ArticleCandidate:
    """Reconstruct the classifier input without fetching or changing sources."""

    return ArticleCandidate(
        article_id=article.article_id,
        slug=article.slug,
        title=article.title,
        doi=article.doi,
        pmid=article.pmid,
        pmcid=article.pmcid,
        openalex_id=article.openalex_id,
        journal=article.journal,
        issn=list(article.issn or []),
        publisher=article.publisher,
        authors=list(article.authors or []),
        article_type=article.article_type,
        study_type=article.study_type,
        published_at=_aware(article.published_at),
        indexed_at=_aware(article.indexed_at),
        abstract_text=article.abstract_text,
        abstract_license=article.abstract_license,
        source_urls=dict(article.source_urls or {}),
        open_access_status=article.open_access_status,
        open_access_url=article.open_access_url,
        license_url=article.license_url,
        peer_review_status=article.peer_review_status,
        integrity_status=article.integrity_status,
        source_payload=dict(article.source_payload or {}),
    )


async def reclassify_existing_literature(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Re-run current rules over stored source metadata and replace only links.

    Editorial publication state, feature selection, summaries, identifiers, and
    source metadata are preserved. No provider request is made.
    """

    config = get_config().literature
    pipeline = LiteraturePipeline(config)
    diseases, countries = await pipeline._classification_catalogues()
    taxonomy = _load_json(ROOT / config.taxonomy_path)
    async with get_db() as db:
        query = select(LiteratureArticle).order_by(LiteratureArticle.id)
        if limit is not None:
            query = query.limit(limit)
        articles = (await db.execute(query)).scalars().all()
        article_ids = [article.article_id for article in articles]
        existing_diseases: dict[str, set[str]] = {article_id: set() for article_id in article_ids}
        existing_countries: dict[str, set[str]] = {article_id: set() for article_id in article_ids}
        existing_topics: dict[str, set[str]] = {article_id: set() for article_id in article_ids}
        if article_ids:
            for row in (await db.execute(select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(article_ids)))).scalars():
                existing_diseases[row.article_id].add(row.disease_id)
            for row in (await db.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids)))).scalars():
                existing_countries[row.article_id].add(row.country_code)
            for row in (await db.execute(select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(article_ids)))).scalars():
                existing_topics[row.article_id].add(row.topic)

        repository = LiteratureRepository(db)
        changed = 0
        countries_added = 0
        diseases_added = 0
        topics_added = 0
        for article in articles:
            metadata = dict(article.metadata_ or {})
            relation_score = float(
                (metadata.get("discovery_score_evidence") or {}).get("surveillance_relation_score")
                or 0.0
            )
            classification = classify_candidate(
                candidate_from_stored_article(article),
                diseases=diseases,
                countries=countries,
                taxonomy=taxonomy,
                auto_publish_min_score=config.auto_publish_min_score,
                surveillance_relation_score=relation_score,
            )
            # Reclassification updates discoverability evidence, never an
            # editor's publication decision.
            classification.publication_status = article.publication_status
            new_diseases = {match.key for match in classification.diseases}
            new_countries = {match.key for match in classification.countries}
            new_topics = {match.key for match in classification.topics}
            differs = (
                new_diseases != existing_diseases[article.article_id]
                or new_countries != existing_countries[article.article_id]
                or new_topics != existing_topics[article.article_id]
                or int(metadata.get("classification_version") or 0) < CLASSIFICATION_VERSION
            )
            changed += int(differs)
            diseases_added += len(new_diseases - existing_diseases[article.article_id])
            countries_added += len(new_countries - existing_countries[article.article_id])
            topics_added += len(new_topics - existing_topics[article.article_id])
            if not dry_run:
                await repository.replace_classification(article, classification)

    signal_links = await reconcile_automatic_signal_links(dry_run=dry_run)
    return {
        "dry_run": dry_run,
        "examined": len(articles),
        "changed": changed,
        "diseases_added": diseases_added,
        "countries_added": countries_added,
        "topics_added": topics_added,
        "classification_version": CLASSIFICATION_VERSION,
        "signal_links": signal_links,
    }


def _automatic_relation(
    *,
    disease_confidence: float,
    country_confidences: dict[str, float],
    signal_country_codes: list[str],
    disease_min_confidence: float,
    exact_min_confidence: float,
    context_min_confidence: float,
) -> tuple[str, float, list[str]]:
    matched_countries = {
        str(code).upper(): float(country_confidences.get(str(code).upper()) or 0.0)
        for code in signal_country_codes
        if float(country_confidences.get(str(code).upper()) or 0.0) >= exact_min_confidence
    }
    if disease_confidence >= disease_min_confidence and matched_countries:
        confidence = min(disease_confidence, max(matched_countries.values()))
        return (
            "exact_disease_geography",
            round(confidence, 3),
            [
                f"classification-v{CLASSIFICATION_VERSION}:disease:{disease_confidence:.3f}",
                *[
                    f"classification-v{CLASSIFICATION_VERSION}:country:{code}:{score:.3f}"
                    for code, score in sorted(matched_countries.items())
                ],
            ],
        )
    if disease_confidence >= context_min_confidence:
        return (
            "disease_context",
            round(disease_confidence, 3),
            [f"classification-v{CLASSIFICATION_VERSION}:disease:{disease_confidence:.3f}"],
        )
    return (
        "candidate",
        round(disease_confidence, 3),
        [f"classification-v{CLASSIFICATION_VERSION}:insufficient-evidence:{disease_confidence:.3f}"],
    )


async def reconcile_automatic_signal_links(*, dry_run: bool = False) -> dict[str, Any]:
    """Re-evaluate only autopilot-owned links after classifier backfill.

    Human-reviewed decisions are deliberately immutable here.  Automatic links
    are upgraded or downgraded from the persisted versioned disease/country
    evidence, and the article's 15% surveillance score component follows the
    strongest confirmed relationship.
    """

    config = get_config().literature
    async with get_db() as db:
        links = list((await db.execute(select(LiteratureSignalArticleLink))).scalars().all())
        automatic_links = [
            link
            for link in links
            if link.source == "gap_discovery"
            and link.gap_id
            and str(((link.metadata_ or {}).get("autopilot") or {}).get("actor") or "")
            == "research-radar-autopilot"
            and (not link.reviewed_by or link.reviewed_by == "research-radar-autopilot")
        ]
        gap_ids = {str(link.gap_id) for link in automatic_links}
        article_ids = {str(link.article_id) for link in automatic_links}
        gaps = {
            gap.gap_id: gap
            for gap in (
                await db.execute(select(LiteratureEvidenceGap).where(LiteratureEvidenceGap.gap_id.in_(gap_ids)))
            ).scalars().all()
        } if gap_ids else {}
        disease_evidence: dict[str, dict[str, float]] = defaultdict(dict)
        country_evidence: dict[str, dict[str, float]] = defaultdict(dict)
        if article_ids:
            for row in (
                await db.execute(select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(article_ids)))
            ).scalars().all():
                disease_evidence[row.article_id][row.disease_id] = float(row.confidence or 0.0)
            for row in (
                await db.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids)))
            ).scalars().all():
                country_evidence[row.article_id][str(row.country_code).upper()] = float(row.confidence or 0.0)

        changed = upgraded = downgraded = skipped = 0
        relation_rank = {"candidate": 0, "disease_context": 1, "exact_disease_geography": 2}
        now = datetime.now(timezone.utc).replace(microsecond=0)
        strongest_confirmed: dict[str, str] = {}
        for link in automatic_links:
            gap = gaps.get(str(link.gap_id))
            if gap is None:
                skipped += 1
                continue
            desired, confidence, reasons = _automatic_relation(
                disease_confidence=disease_evidence[link.article_id].get(gap.disease_id, 0.0),
                country_confidences=country_evidence[link.article_id],
                signal_country_codes=list(gap.country_codes or []),
                disease_min_confidence=config.autopilot_disease_min_confidence,
                exact_min_confidence=config.autopilot_exact_relation_min_confidence,
                context_min_confidence=config.autopilot_context_relation_min_confidence,
            )
            desired_status = "confirmed" if desired != "candidate" else "review"
            if relation_rank.get(desired, 0) > relation_rank.get(link.relation_level, 0):
                upgraded += 1
            elif relation_rank.get(desired, 0) < relation_rank.get(link.relation_level, 0):
                downgraded += 1
            differs = (
                link.relation_level != desired
                or link.status != desired_status
                or round(float(link.confidence or 0.0), 3) != confidence
            )
            changed += int(differs)
            if not dry_run and differs:
                previous = {
                    "relation_level": link.relation_level,
                    "status": link.status,
                    "confidence": link.confidence,
                }
                link.relation_level = desired
                link.status = desired_status
                link.confidence = confidence
                link.match_reasons = reasons
                link.reviewed_at = now
                link.reviewed_by = "research-radar-autopilot"
                link.review_note = f"Reconciled from classification v{CLASSIFICATION_VERSION}."
                link.metadata_ = {
                    **(link.metadata_ or {}),
                    "classification_reconciliation": {
                        "classification_version": CLASSIFICATION_VERSION,
                        "reconciled_at": now.isoformat(),
                        "previous": previous,
                    },
                }
            effective_status = desired_status if differs else link.status
            effective_relation = desired if differs else link.relation_level
            if effective_status == "confirmed" and relation_rank.get(effective_relation, 0) > relation_rank.get(
                strongest_confirmed.get(link.article_id, "candidate"), 0
            ):
                strongest_confirmed[link.article_id] = effective_relation

        if not dry_run:
            strongest_confirmed = {}
            for persisted_link in links:
                if persisted_link.status != "confirmed":
                    continue
                current = strongest_confirmed.get(persisted_link.article_id, "candidate")
                if relation_rank.get(persisted_link.relation_level, 0) > relation_rank.get(current, 0):
                    strongest_confirmed[persisted_link.article_id] = persisted_link.relation_level

        if not dry_run and strongest_confirmed:
            articles = list((
                await db.execute(select(LiteratureArticle).where(LiteratureArticle.article_id.in_(strongest_confirmed)))
            ).scalars().all())
            for article in articles:
                metadata = dict(article.metadata_ or {})
                discovery = dict(metadata.get("discovery_score_evidence") or {})
                components = {
                    key: dict(value)
                    for key, value in (discovery.get("components") or {}).items()
                    if isinstance(value, dict)
                }
                relation_level = strongest_confirmed[article.article_id]
                relation_score = surveillance_relation_score_for_level(relation_level)
                components["surveillance_relation"] = {
                    "value": relation_score,
                    "weight": 0.15,
                    "contribution": round(relation_score * 0.15, 4),
                }
                article.discovery_score = round(sum(float(row.get("contribution") or 0.0) for row in components.values()), 3)
                metadata["discovery_score_evidence"] = {
                    **discovery,
                    "surveillance_relation_level": relation_level,
                    "surveillance_relation_score": relation_score,
                    "components": components,
                }
                article.metadata_ = metadata

    return {
        "dry_run": dry_run,
        "examined": len(automatic_links),
        "changed": changed,
        "upgraded": upgraded,
        "downgraded": downgraded,
        "skipped": skipped,
        "classification_version": CLASSIFICATION_VERSION,
    }


__all__ = [
    "candidate_from_stored_article",
    "reclassify_existing_literature",
    "reconcile_automatic_signal_links",
]
