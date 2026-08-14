"""Transactional persistence for normalized and classified literature records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureStatusEvent,
    LiteratureTopicLink,
)

from .types import ArticleCandidate, Classification


class LiteratureRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(
        self,
        candidate: ArticleCandidate,
        classification: Classification,
        *,
        discovery_context: dict[str, Any] | None = None,
        new_publication_status: str | None = None,
    ) -> bool:
        article = await self._find(candidate)
        inserted = article is None
        if article is None:
            article = LiteratureArticle(article_id=candidate.article_id, slug=candidate.slug, title=candidate.title)
            self.db.add(article)
            previous_integrity = None
        else:
            previous_integrity = article.integrity_status

        existing_metadata = dict(article.metadata_ or {})
        editorial_locked = bool(existing_metadata.get("editorial_locked"))
        autopilot_locked = bool(
            existing_metadata.get("autopilot", {}).get("decision") == "publish"
            and candidate.integrity_status == "current"
        )
        article.doi = candidate.doi
        article.pmid = candidate.pmid or article.pmid
        article.pmcid = candidate.pmcid or article.pmcid
        article.title = candidate.title
        article.journal = candidate.journal
        article.issn = candidate.issn
        article.publisher = candidate.publisher
        article.authors = candidate.authors
        article.article_type = candidate.article_type
        article.study_type = classification.study_type
        article.published_at = candidate.published_at
        article.indexed_at = candidate.indexed_at
        article.abstract_text = candidate.abstract_text
        article.abstract_license = candidate.abstract_license
        article.source_urls = candidate.source_urls
        article.open_access_status = candidate.open_access_status
        article.open_access_url = candidate.open_access_url
        article.license_url = candidate.license_url
        article.peer_review_status = candidate.peer_review_status
        article.integrity_status = candidate.integrity_status
        article.relevance_score = classification.relevance_score
        article.public_health_score = classification.public_health_score
        article.discovery_score = classification.discovery_score
        article.source_payload = candidate.source_payload
        metadata = dict(article.metadata_ or {})
        if discovery_context:
            origins = [
                item
                for item in metadata.get("discovery_origins") or []
                if item.get("gap_id") != discovery_context.get("gap_id")
            ]
            origins.append(discovery_context)
            metadata["discovery_origins"] = origins[-20:]
        article.metadata_ = {
            **metadata,
            "classification_version": 2,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }
        if not editorial_locked and not autopilot_locked:
            article.publication_status = (
                new_publication_status
                if inserted and new_publication_status is not None
                else classification.publication_status
            )

        await self.db.flush()
        await self._replace_links(candidate.article_id, classification)
        if inserted:
            self.db.add(LiteratureStatusEvent(
                article_id=candidate.article_id,
                event_type="indexed",
                current_status=candidate.integrity_status,
                source="crossref",
                effective_at=candidate.indexed_at,
                metadata_={},
            ))
        elif previous_integrity != candidate.integrity_status:
            self.db.add(LiteratureStatusEvent(
                article_id=candidate.article_id,
                event_type="integrity_status_changed",
                previous_status=previous_integrity,
                current_status=candidate.integrity_status,
                source="crossref",
                effective_at=candidate.indexed_at,
                metadata_={},
            ))
        return inserted

    async def _find(self, candidate: ArticleCandidate) -> LiteratureArticle | None:
        if candidate.doi:
            article = (
                await self.db.execute(select(LiteratureArticle).where(LiteratureArticle.doi == candidate.doi))
            ).scalar_one_or_none()
            if article is not None:
                candidate.article_id = article.article_id
                candidate.slug = article.slug
                return article
        return (
            await self.db.execute(
                select(LiteratureArticle).where(LiteratureArticle.article_id == candidate.article_id)
            )
        ).scalar_one_or_none()

    async def _replace_links(self, article_id: str, classification: Classification) -> None:
        for model in (LiteratureDiseaseLink, LiteratureCountryLink, LiteratureTopicLink):
            await self.db.execute(delete(model).where(model.article_id == article_id))
        self.db.add_all([
            LiteratureDiseaseLink(
                article_id=article_id,
                disease_id=match.key,
                confidence=match.confidence,
                match_terms=match.terms,
            )
            for match in classification.diseases
        ])
        self.db.add_all([
            LiteratureCountryLink(
                article_id=article_id,
                country_code=match.key,
                country_name=match.label,
                confidence=match.confidence,
            )
            for match in classification.countries
        ])
        self.db.add_all([
            LiteratureTopicLink(article_id=article_id, topic=match.key, confidence=match.confidence)
            for match in classification.topics
        ])


__all__ = ["LiteratureRepository"]
