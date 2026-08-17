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

from .classification import CLASSIFICATION_VERSION
from .types import ArticleCandidate, Classification


def _merge_version_relations(
    existing: list[Any],
    incoming: list[dict[str, str]],
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for value in [*existing, *incoming]:
        if not isinstance(value, dict):
            continue
        preprint_doi = str(value.get("preprint_doi") or "").strip().lower()
        peer_reviewed_doi = str(value.get("peer_reviewed_doi") or "").strip().lower()
        if not preprint_doi or not peer_reviewed_doi or preprint_doi == peer_reviewed_doi:
            continue
        key = (preprint_doi, peer_reviewed_doi)
        merged[key] = {
            **merged.get(key, {}),
            **{str(item_key): str(item_value) for item_key, item_value in value.items() if item_value},
            "relation_type": "preprint_to_peer_reviewed",
            "preprint_doi": preprint_doi,
            "peer_reviewed_doi": peer_reviewed_doi,
        }
    return [merged[key] for key in sorted(merged)]


def _classification_metadata(
    existing: dict[str, Any],
    classification: Classification,
) -> dict[str, Any]:
    def matches(values: list[Any], *, include_label: bool = False) -> dict[str, dict[str, Any]]:
        return {
            match.key: {
                **({"label": match.label} if include_label else {}),
                "confidence": match.confidence,
                "matched_terms": match.terms,
            }
            for match in values
        }

    return {
        **existing,
        "classification_version": CLASSIFICATION_VERSION,
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "discovery_score_evidence": {
            "surveillance_relation_level": classification.surveillance_relation_level,
            "surveillance_relation_score": classification.surveillance_relation_score,
            "components": classification.discovery_score_components,
        },
        "classification_evidence": {
            "diseases": matches(classification.diseases),
            "countries": matches(classification.countries),
            "topics": matches(classification.topics),
            "pathogens": matches(classification.pathogens, include_label=True),
            "pathogen_types": matches(classification.pathogen_types, include_label=True),
            "populations": matches(classification.populations, include_label=True),
            "research_domain": {
                "value": classification.research_domain,
                "matched_terms": classification.research_domain_terms,
            },
        },
    }


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
        preserve_existing_publication_status: bool = False,
    ) -> bool:
        article = await self._find(candidate)
        inserted = article is None
        if article is None:
            article = LiteratureArticle(article_id=candidate.article_id, slug=candidate.slug, title=candidate.title)
            self.db.add(article)
            previous_integrity = None
            previous_publication_status = None
        else:
            previous_integrity = article.integrity_status
            previous_publication_status = article.publication_status

        existing_metadata = dict(article.metadata_ or {})
        editorial_locked = bool(existing_metadata.get("editorial_locked"))
        autopilot_locked = bool(
            existing_metadata.get("autopilot", {}).get("decision") == "publish"
            and candidate.integrity_status == "current"
        )
        existing_oa_status = article.open_access_status or "unknown"
        existing_oa_url = article.open_access_url
        article.doi = candidate.doi or article.doi
        article.pmid = candidate.pmid or article.pmid
        article.pmcid = candidate.pmcid or article.pmcid
        article.openalex_id = candidate.openalex_id or article.openalex_id
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
        article.abstract_license = candidate.abstract_license or article.abstract_license
        article.source_urls = {**(article.source_urls or {}), **candidate.source_urls}
        if candidate.open_access_status == "open" or existing_oa_status == "unknown":
            article.open_access_status = candidate.open_access_status
        else:
            article.open_access_status = existing_oa_status
        article.open_access_url = candidate.open_access_url or existing_oa_url
        article.license_url = candidate.license_url or article.license_url
        article.peer_review_status = candidate.peer_review_status
        article.integrity_status = candidate.integrity_status
        article.relevance_score = classification.relevance_score
        article.public_health_score = classification.public_health_score
        article.discovery_score = classification.discovery_score
        article.source_payload = {**(article.source_payload or {}), **candidate.source_payload}
        metadata = dict(article.metadata_ or {})
        if candidate.version_relations:
            mapped_relations = await self._map_version_relations(article, candidate.version_relations)
            metadata["version_relations"] = _merge_version_relations(
                list(metadata.get("version_relations") or []),
                mapped_relations,
            )
        if discovery_context:
            origins = [
                item
                for item in metadata.get("discovery_origins") or []
                if item.get("gap_id") != discovery_context.get("gap_id")
            ]
            origins.append(discovery_context)
            metadata["discovery_origins"] = origins[-20:]
        article.metadata_ = _classification_metadata(metadata, classification)
        if (
            not editorial_locked
            and not autopilot_locked
            and not (
                preserve_existing_publication_status
                and not inserted
                and candidate.integrity_status == "current"
                and classification.publication_status != "excluded"
            )
        ):
            next_publication_status = (
                new_publication_status
                if inserted and new_publication_status is not None
                else classification.publication_status
            )
            article.publication_status = next_publication_status
            existing_autopilot = existing_metadata.get("autopilot") or {}
            if (
                previous_publication_status == "excluded"
                and next_publication_status == "review"
                and isinstance(existing_autopilot, dict)
                and existing_autopilot.get("decision") == "exclude"
            ):
                # A newer classification may legitimately reopen an old
                # automatic exclusion. Never leave the previous exclusion as
                # the apparent final decision while the row is back in review.
                reopened_at = datetime.now(timezone.utc).replace(microsecond=0)
                reopen_reason = (
                    "new classification evidence reopened the prior automatic exclusion for review"
                )
                article.metadata_ = {
                    **(article.metadata_ or {}),
                    "autopilot": {
                        **existing_autopilot,
                        "decision": "hold",
                        "decided_at": reopened_at.isoformat(),
                        "actor": "literature-classifier",
                        "reopened_from": "exclude",
                        "reasons": [reopen_reason],
                    },
                }
                self.db.add(LiteratureStatusEvent(
                    article_id=article.article_id,
                    event_type="publication_status_changed",
                    previous_status="excluded",
                    current_status="review",
                    source="literature-classifier",
                    effective_at=reopened_at,
                    metadata_={
                        "previous_automatic_decision": "exclude",
                        "reason": reopen_reason,
                    },
                ))

        await self.db.flush()
        await self._replace_links(candidate.article_id, classification)
        has_rss = "rss" in candidate.source_payload
        has_official_guidance = "official_guidance" in candidate.source_payload
        has_crossref = any(key in candidate.source_payload for key in ("DOI", "indexed", "container-title"))
        event_source = (
            "crossref+publisher-rss" if has_rss and has_crossref
            else "publisher-rss" if has_rss
            else "who-iris-oai" if has_official_guidance
            else "crossref"
        )
        if inserted:
            self.db.add(LiteratureStatusEvent(
                article_id=candidate.article_id,
                event_type="indexed",
                current_status=candidate.integrity_status,
                source=event_source,
                effective_at=candidate.indexed_at,
                metadata_={},
            ))
        elif previous_integrity != candidate.integrity_status:
            self.db.add(LiteratureStatusEvent(
                article_id=candidate.article_id,
                event_type="integrity_status_changed",
                previous_status=previous_integrity,
                current_status=candidate.integrity_status,
                source=event_source,
                effective_at=candidate.indexed_at,
                metadata_={},
            ))
        return inserted

    async def _map_version_relations(
        self,
        article: LiteratureArticle,
        relations: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Attach stable article IDs when the related DOI is already indexed."""

        mapped_relations: list[dict[str, str]] = []
        current_doi = str(article.doi or "").strip().lower()
        for relation in _merge_version_relations([], relations):
            mapped = dict(relation)
            if current_doi == mapped["preprint_doi"]:
                mapped["preprint_article_id"] = article.article_id
                target_doi = mapped["peer_reviewed_doi"]
                target_key = "peer_reviewed_article_id"
            elif current_doi == mapped["peer_reviewed_doi"]:
                mapped["peer_reviewed_article_id"] = article.article_id
                target_doi = mapped["preprint_doi"]
                target_key = "preprint_article_id"
            else:
                mapped_relations.append(mapped)
                continue
            target = (
                await self.db.execute(
                    select(LiteratureArticle).where(LiteratureArticle.doi == target_doi)
                )
            ).scalar_one_or_none()
            if target is not None and target.article_id != article.article_id:
                mapped[target_key] = target.article_id
                target_metadata = dict(target.metadata_ or {})
                target_metadata["version_relations"] = _merge_version_relations(
                    list(target_metadata.get("version_relations") or []),
                    [mapped],
                )
                target.metadata_ = target_metadata
            mapped_relations.append(mapped)
        return mapped_relations

    async def replace_classification(
        self,
        article: LiteratureArticle,
        classification: Classification,
    ) -> None:
        """Replace classifier-owned fields without touching editorial state."""

        article.study_type = classification.study_type
        article.relevance_score = classification.relevance_score
        article.public_health_score = classification.public_health_score
        article.discovery_score = classification.discovery_score
        article.metadata_ = _classification_metadata(dict(article.metadata_ or {}), classification)
        await self.db.flush()
        await self._replace_links(article.article_id, classification)

    async def _find(self, candidate: ArticleCandidate) -> LiteratureArticle | None:
        # Prefer stable, provider-issued identifiers. Deliberately avoid fuzzy
        # title matching: same-title articles in the same year are common and
        # an incorrect automatic merge is difficult to unwind safely.
        lookups = (
            (LiteratureArticle.doi, candidate.doi),
            (LiteratureArticle.pmid, candidate.pmid),
            (LiteratureArticle.pmcid, candidate.pmcid),
            (LiteratureArticle.openalex_id, candidate.openalex_id),
        )
        for column, value in lookups:
            if not value:
                continue
            article = (
                await self.db.execute(select(LiteratureArticle).where(column == value))
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
