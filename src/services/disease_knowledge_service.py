"""Disease knowledge update service used by scripts, worker tasks, and dashboard APIs."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import DiseaseKnowledgeBrief, DiseaseKnowledgeSource, Task, TaskStatus
from src.knowledge import (
    AIDiseaseBriefGenerator,
    DiseaseKnowledgeFetcher,
    SourceGroundedBriefGenerator,
    build_catalogue_disease_brief_payload,
    knowledge_brief_fallback_reason,
    knowledge_brief_publication_tier,
    resolve_disease_knowledge_status,
)
from src.knowledge.citations import normalize_knowledge_citations
from src.services.exceptions import TaskCancelledError

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISEASE_CSV = ROOT / "configs" / "standard_diseases.csv"

SOURCE_GROUPS = {
    "who": ["who", "who_don"],
    "who_don": ["who_don"],
    "search": ["web_search"],
    "web": ["web_search"],
    "web_search": ["web_search"],
    "wikidata": ["wikidata"],
    "wikipedia": ["wikipedia"],
    "pubmed": ["pubmed"],
    "msd": ["msd"],
}


def load_standard_diseases(path: Path = DEFAULT_DISEASE_CSV) -> list[dict[str, Any]]:
    """Load the standard disease catalogue from CSV."""
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for raw in csv.DictReader(csv_file):
            rows.append(
                {
                    "disease_id": raw["disease_id"],
                    "name_en": raw.get("standard_name_en") or raw.get("name_en"),
                    "name_zh": raw.get("standard_name_zh") or raw.get("name_zh"),
                    "standard_name_en": raw.get("standard_name_en"),
                    "standard_name_zh": raw.get("standard_name_zh"),
                    "category": raw.get("category") or "Unknown",
                    "icd_10": raw.get("icd_10"),
                    "icd_11": raw.get("icd_11"),
                    "description": raw.get("description") or "",
                    "source": raw.get("source") or "",
                }
            )
    return rows


def expand_sources(values: list[str] | None) -> list[str]:
    """Expand source groups into concrete adapters."""
    if not values:
        values = ["who", "search", "wikidata", "wikipedia", "pubmed", "msd"]
    expanded: list[str] = []
    for value in values:
        for adapter in SOURCE_GROUPS.get(value, []):
            if adapter not in expanded:
                expanded.append(adapter)
    return expanded


async def _existing_sources(db, disease_id: str) -> list[DiseaseKnowledgeSource]:
    result = await db.execute(
        select(DiseaseKnowledgeSource)
        .where(DiseaseKnowledgeSource.disease_id == disease_id)
        .order_by(DiseaseKnowledgeSource.source_type, DiseaseKnowledgeSource.id)
    )
    return list(result.scalars().all())


async def _upsert_source(db, candidate) -> DiseaseKnowledgeSource:
    result = await db.execute(
        select(DiseaseKnowledgeSource).where(
            DiseaseKnowledgeSource.disease_id == candidate.disease_id,
            DiseaseKnowledgeSource.source_type == candidate.source_type,
            DiseaseKnowledgeSource.url == candidate.url,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = DiseaseKnowledgeSource(
            disease_id=candidate.disease_id,
            source_type=candidate.source_type,
            source_name=candidate.source_name,
            url=candidate.url,
        )
        db.add(row)

    row.title = candidate.title
    row.license = candidate.license
    row.status = candidate.status
    row.language = candidate.language
    row.resolved_url = getattr(candidate, "resolved_url", None) or candidate.url
    row.raw_excerpt = candidate.raw_excerpt
    row.content_text = getattr(candidate, "content_text", None)
    row.content_sections = getattr(candidate, "content_sections", None) or []
    row.raw_excerpt_hash = candidate.raw_excerpt_hash
    row.fetched_at = candidate.fetched_at
    row.review_status = candidate.review_status
    row.metadata_ = {
        **(candidate.metadata or {}),
        "resolved_url": getattr(candidate, "resolved_url", None) or candidate.url,
    }
    await db.flush()
    return row


async def _mark_stale_sources(db, disease_id: str, candidates: list, enabled_sources: list[str]) -> None:
    """Reject old rows from refreshed source adapters that no longer match."""
    if not candidates:
        return
    fresh_keys = {(c.disease_id, c.source_type, c.url) for c in candidates}
    source_rows = await _existing_sources(db, disease_id)
    for row in source_rows:
        if row.source_type not in enabled_sources:
            continue
        if (row.disease_id, row.source_type, row.url) in fresh_keys:
            continue
        row.status = "stale"
        row.review_status = "rejected"
        row.metadata_ = {**(row.metadata_ or {}), "stale_reason": "Not returned by latest forced refresh"}


async def _upsert_brief(db, payload: dict[str, Any]) -> DiseaseKnowledgeBrief:
    payload = normalize_knowledge_citations(payload)
    result = await db.execute(
        select(DiseaseKnowledgeBrief).where(
            DiseaseKnowledgeBrief.disease_id == payload["disease_id"],
            DiseaseKnowledgeBrief.language == payload["language"],
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = DiseaseKnowledgeBrief(
            disease_id=payload["disease_id"],
            language=payload["language"],
            brief=payload["brief"],
        )
        db.add(row)

    row.brief = payload["brief"]
    row.definition = payload.get("definition")
    row.clinical_features = payload.get("clinical_features")
    row.epidemiology = payload.get("epidemiology")
    row.clinical_summary = payload.get("clinical_summary")
    row.transmission = payload.get("transmission")
    row.prevention = payload.get("prevention")
    row.surveillance_note = payload.get("surveillance_note")
    row.risk_groups = payload.get("risk_groups")
    row.source_ids = payload.get("source_ids") or []
    row.source_attribution = payload.get("source_attribution") or []
    row.disclaimer = payload.get("disclaimer")
    row.model = payload.get("model")
    row.status = payload.get("status") or "draft"
    row.source_confidence = payload.get("source_confidence") or "medium"
    row.quality_score = payload.get("quality_score")
    row.review_notes = payload.get("review_notes")
    row.metadata_ = payload.get("metadata") or {}
    await db.flush()
    return row


def source_to_dict(row: DiseaseKnowledgeSource) -> dict[str, Any]:
    metadata = row.metadata_ or {}
    return {
        "id": row.id,
        "disease_id": row.disease_id,
        "source_type": row.source_type,
        "source_name": row.source_name,
        "url": row.url,
        "resolved_url": row.resolved_url or metadata.get("resolved_url") or row.url,
        "title": row.title,
        "license": row.license,
        "status": row.status,
        "language": row.language,
        "raw_excerpt": row.raw_excerpt,
        "content_text": row.content_text or metadata.get("content_text"),
        "content_sections": row.content_sections or metadata.get("content_sections") or [],
        "raw_excerpt_hash": row.raw_excerpt_hash,
        "review_status": row.review_status,
        "metadata": metadata,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metadata_field(source: dict[str, Any], key: str) -> Any:
    metadata = source.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _enrich_source_attribution(
    attribution: list[Any],
    sources_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    metadata_keys = (
        "pmid",
        "doi",
        "first_author",
        "journal",
        "pub_date",
        "container_title",
        "publisher",
        "year",
        "provider",
        "content_kind",
    )
    direct_keys = (
        "source_name",
        "source_type",
        "title",
        "url",
        "resolved_url",
        "license",
        "fetched_at",
    )

    for item in attribution:
        if not isinstance(item, dict):
            continue
        source_id = _safe_int(item.get("source_id") or item.get("id"))
        source = sources_by_id.get(source_id) if source_id is not None else None
        if not source:
            enriched.append(dict(item))
            continue

        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        merged = {
            **item,
            "id": item.get("id") or source.get("id"),
            "source_id": item.get("source_id") or source.get("id"),
            "metadata": {**source_metadata, **item_metadata},
        }
        for key in direct_keys:
            if not merged.get(key):
                merged[key] = source.get(key)
        for key in metadata_keys:
            if not merged.get(key):
                merged[key] = _metadata_field(source, key)
        enriched.append(merged)
    return enriched


def _has_approved_public_sources(sources: list[dict[str, Any]]) -> bool:
    return any(
        str(src.get("source_type") or "") in SourceGroundedBriefGenerator.PUBLIC_SOURCE_TYPES
        and str(src.get("review_status") or "pending") != "rejected"
        for src in sources
    )


def _catalogue_fallback_reason(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "no_sources_fetched"
    if all(str(src.get("source_type") or "") == "msd" for src in sources):
        return "metadata_only_sources"
    return "no_public_sources"


async def _generate_brief(
    generator: AIDiseaseBriefGenerator | SourceGroundedBriefGenerator,
    *,
    disease: dict[str, Any],
    sources: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    result = generator.generate(disease=disease, sources=sources, language=language)
    if hasattr(result, "__await__"):
        return await result
    return result


async def _generate_brief_result(
    generator: AIDiseaseBriefGenerator | SourceGroundedBriefGenerator,
    *,
    disease: dict[str, Any],
    sources: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    if hasattr(generator, "generate_with_trace"):
        result = generator.generate_with_trace(disease=disease, sources=sources, language=language)
        if hasattr(result, "__await__"):
            return await result
        return result

    payload = await _generate_brief(generator, disease=disease, sources=sources, language=language)
    return {
        "payload": payload,
        "trace": {
            "generator": "template",
            "language": language,
            "preferred_models": [],
            "shard_index": 0,
            "shard_key": f"{str(disease.get('disease_id') or '').strip().upper()}:{language}",
            "model": None,
            "provider": None,
            "token_usage": {},
            "duration": 0.0,
            "prompt": None,
            "system_prompt": None,
            "response": None,
            "error": None,
            "cache_hit": False,
        },
    }


def _normalize_generator_mode(mode: str | None) -> str:
    value = (mode or "ai").strip().lower()
    if value not in {"ai", "auto", "template"}:
        return "ai"
    return value


def _generator_for_mode(mode: str) -> AIDiseaseBriefGenerator | SourceGroundedBriefGenerator:
    if mode == "template":
        return SourceGroundedBriefGenerator()
    return AIDiseaseBriefGenerator()


async def _log_task(
    task_uuid: Optional[str],
    *,
    entry_type: str,
    title: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    success: bool = True,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    model_used: Optional[str] = None,
    tokens_used: Optional[int] = None,
    duration: Optional[float] = None,
) -> None:
    if not task_uuid:
        return
    await task_manager.add_workbook_entry(
        task_uuid,
        entry_type=entry_type,
        title=title,
        content=content,
        content_type="text",
        prompt=prompt,
        response=response,
        model_used=model_used,
        tokens_used=tokens_used,
        duration=duration,
        success=success,
        metadata=metadata or {},
    )


def _total_tokens_from_usage(token_usage: Any) -> Optional[int]:
    if not isinstance(token_usage, dict):
        return None
    total = token_usage.get("total")
    if isinstance(total, int):
        return total
    if isinstance(total, float):
        return int(total)
    return None


async def _raise_if_cancelled(task_uuid: Optional[str]) -> None:
    if task_uuid and await task_manager.is_cancel_requested(task_uuid):
        raise TaskCancelledError("Cancellation requested by user")


class DiseaseKnowledgeUpdateService:
    """Coordinates source fetch, brief generation, and persistence for one disease."""

    def __init__(
        self,
        *,
        disease_csv_path: Path = DEFAULT_DISEASE_CSV,
        fetcher: DiseaseKnowledgeFetcher | None = None,
    ) -> None:
        self.disease_csv_path = disease_csv_path
        self.fetcher = fetcher or DiseaseKnowledgeFetcher()
        self.ai_generator = AIDiseaseBriefGenerator()
        self.template_generator = SourceGroundedBriefGenerator()
        self._disease_cache: list[dict[str, Any]] | None = None

    def load_standard_diseases(self) -> list[dict[str, Any]]:
        if self._disease_cache is not None:
            return list(self._disease_cache)
        self._disease_cache = load_standard_diseases(self.disease_csv_path)
        return list(self._disease_cache)

    def _find_disease(self, disease_id: str) -> dict[str, Any]:
        wanted = (disease_id or "").strip().upper()
        for row in self.load_standard_diseases():
            if str(row.get("disease_id") or "").upper() == wanted:
                return row
        raise ValueError(f"Disease not found in standard catalogue: {disease_id}")

    async def list_catalogue(self, db, *, search: str | None = None) -> list[dict[str, Any]]:
        diseases = self.load_standard_diseases()
        if search:
            needle = search.strip().lower()
            if needle:
                diseases = [
                    disease
                    for disease in diseases
                    if needle in str(disease.get("disease_id") or "").lower()
                    or needle in str(disease.get("name_en") or "").lower()
                    or needle in str(disease.get("name_zh") or "").lower()
                    or needle in str(disease.get("description") or "").lower()
                    or needle in str(disease.get("category") or "").lower()
                ]

        brief_rows = (
            await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(DiseaseKnowledgeBrief.status.in_(["published", "draft", "requires_review"]))
                .order_by(DiseaseKnowledgeBrief.disease_id.asc(), DiseaseKnowledgeBrief.language.asc())
            )
        ).scalars().all()
        source_counts_rows = (
            await db.execute(
                select(
                    DiseaseKnowledgeSource.disease_id,
                    func.count(DiseaseKnowledgeSource.id).label("source_count"),
                )
                .where(DiseaseKnowledgeSource.review_status != "rejected")
                .group_by(DiseaseKnowledgeSource.disease_id)
            )
        ).all()

        briefs_by_disease: dict[str, dict[str, DiseaseKnowledgeBrief]] = {}
        for brief in brief_rows:
            briefs_by_disease.setdefault(brief.disease_id, {})[brief.language] = brief

        source_counts = {row.disease_id: int(row.source_count or 0) for row in source_counts_rows}

        items: list[dict[str, Any]] = []
        for disease in diseases:
            disease_id = str(disease.get("disease_id") or "")
            language_briefs = briefs_by_disease.get(disease_id, {})
            published_languages = sorted(
                [
                    lang
                    for lang, brief in language_briefs.items()
                    if knowledge_brief_publication_tier(brief) == "published"
                ]
            )
            fallback_languages = sorted(
                [
                    lang
                    for lang, brief in language_briefs.items()
                    if knowledge_brief_publication_tier(brief) == "fallback"
                ]
            )
            knowledge_status = resolve_disease_knowledge_status(language_briefs.values())

            latest_updated_at = None
            for brief in language_briefs.values():
                if brief.updated_at and (latest_updated_at is None or brief.updated_at > latest_updated_at):
                    latest_updated_at = brief.updated_at

            items.append(
                {
                    "disease_id": disease_id,
                    "name_en": disease.get("name_en"),
                    "name_zh": disease.get("name_zh"),
                    "category": disease.get("category"),
                    "icd_10": disease.get("icd_10"),
                    "icd_11": disease.get("icd_11"),
                    "description": disease.get("description"),
                    "slug": disease.get("slug"),
                    "knowledge_status": knowledge_status,
                    "knowledge_updated_at": latest_updated_at.isoformat() if latest_updated_at else None,
                    "published_languages": published_languages,
                    "fallback_languages": fallback_languages,
                    "source_count": source_counts.get(disease_id, 0),
                    "brief_statuses": {
                        lang: brief.status for lang, brief in sorted(language_briefs.items())
                    },
                    "brief_tiers": {
                        lang: knowledge_brief_publication_tier(brief)
                        for lang, brief in sorted(language_briefs.items())
                    },
                }
            )
        return items

    async def get_detail(self, db, disease_id: str) -> dict[str, Any]:
        """Return a knowledge-base detail payload for one disease."""
        disease = self._find_disease(disease_id)
        disease_code = str(disease.get("disease_id") or "").upper()

        brief_rows = (
            await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(DiseaseKnowledgeBrief.disease_id == disease_code)
                .order_by(DiseaseKnowledgeBrief.language.asc())
            )
        ).scalars().all()
        source_rows = (
            await db.execute(
                select(DiseaseKnowledgeSource)
                .where(DiseaseKnowledgeSource.disease_id == disease_code)
                .order_by(DiseaseKnowledgeSource.review_status.asc(), DiseaseKnowledgeSource.source_type.asc(), DiseaseKnowledgeSource.id.asc())
            )
        ).scalars().all()
        source_dicts = [source_to_dict(source) for source in source_rows]
        sources_by_id = {
            int(source["id"]): source
            for source in source_dicts
            if source.get("id") is not None
        }

        published_languages = sorted(
            [brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "published"]
        )
        fallback_languages = sorted(
            [brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "fallback"]
        )
        knowledge_status = resolve_disease_knowledge_status(brief_rows)

        latest_updated_at = None
        for brief in brief_rows:
            if brief.updated_at and (latest_updated_at is None or brief.updated_at > latest_updated_at):
                latest_updated_at = brief.updated_at

        brief_statuses = {brief.language: brief.status for brief in brief_rows}
        brief_tiers = {brief.language: knowledge_brief_publication_tier(brief) for brief in brief_rows}
        review_status_counts: dict[str, int] = {}
        source_type_counts: dict[str, int] = {}
        for source in source_rows:
            review_status_counts[source.review_status] = review_status_counts.get(source.review_status, 0) + 1
            source_type_counts[source.source_type] = source_type_counts.get(source.source_type, 0) + 1

        return {
            "disease_id": disease_code,
            "name_en": disease.get("name_en"),
            "name_zh": disease.get("name_zh"),
            "category": disease.get("category"),
            "icd_10": disease.get("icd_10"),
            "icd_11": disease.get("icd_11"),
            "description": disease.get("description"),
            "slug": disease.get("slug"),
            "knowledge_status": knowledge_status,
            "knowledge_updated_at": latest_updated_at.isoformat() if latest_updated_at else None,
            "published_languages": published_languages,
            "fallback_languages": fallback_languages,
            "source_count": len(source_rows),
            "brief_statuses": brief_statuses,
            "brief_tiers": brief_tiers,
            "summary": {
                "brief_count": len(brief_rows),
                "source_count": len(source_rows),
                "published_briefs": sum(1 for brief in brief_rows if knowledge_brief_publication_tier(brief) == "published"),
                "fallback_briefs": sum(1 for brief in brief_rows if knowledge_brief_publication_tier(brief) == "fallback"),
                "draft_briefs": sum(1 for brief in brief_rows if brief.status == "draft"),
                "review_briefs": sum(1 for brief in brief_rows if brief.status == "requires_review"),
                "source_review_counts": review_status_counts,
                "source_type_counts": source_type_counts,
            },
            "briefs": [
                normalize_knowledge_citations(
                    {
                        "language": brief.language,
                        "status": brief.status,
                        "brief_tier": knowledge_brief_publication_tier(brief),
                        "fallback_reason": knowledge_brief_fallback_reason(brief),
                        "source_confidence": brief.source_confidence,
                        "updated_at": brief.updated_at.isoformat() if brief.updated_at else None,
                        "brief": brief.brief,
                        "definition": brief.definition,
                        "clinical_features": brief.clinical_features,
                        "clinical_summary": brief.clinical_summary,
                        "epidemiology": brief.epidemiology,
                        "transmission": brief.transmission,
                        "prevention": brief.prevention,
                        "surveillance_note": brief.surveillance_note,
                        "risk_groups": brief.risk_groups,
                        "disclaimer": brief.disclaimer,
                        "model": brief.model,
                        "quality_score": brief.quality_score,
                        "review_notes": brief.review_notes,
                        "source_ids": brief.source_ids or [],
                        "source_attribution": _enrich_source_attribution(
                            brief.source_attribution or [],
                            sources_by_id,
                        ),
                        "metadata": brief.metadata_ or {},
                    }
                )
                for brief in brief_rows
            ],
            "sources": source_dicts,
        }

    async def update_disease(
        self,
        disease_id: str,
        *,
        enabled_sources: list[str] | None = None,
        force: bool = False,
        generator_mode: str = "ai",
        dry_run: bool = False,
        task_uuid: str | None = None,
    ) -> dict[str, Any]:
        disease = self._find_disease(disease_id)
        generator_mode = _normalize_generator_mode(generator_mode)
        enabled_sources = expand_sources(enabled_sources)
        generator = _generator_for_mode(generator_mode)

        await _log_task(
            task_uuid,
            entry_type="info",
            title="Knowledge Update Started",
            content=(
                f"Disease: {disease['disease_id']}\n"
                f"Source groups: {', '.join(enabled_sources) or 'none'}\n"
                f"Force refresh: {'yes' if force else 'no'}\n"
                f"Generator: {generator_mode}\n"
                f"Dry run: {'yes' if dry_run else 'no'}"
            ),
            metadata={
                "disease_id": disease["disease_id"],
                "source_groups": enabled_sources,
                "force": force,
                "generator": generator_mode,
                "dry_run": dry_run,
                "workflow_stage": "knowledge_start",
            },
        )

        await _raise_if_cancelled(task_uuid)
        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 5)

        if dry_run:
            candidates = await asyncio.to_thread(self.fetcher.fetch, disease, enabled_sources=enabled_sources)
            source_dicts = [
                {
                    "id": idx + 1,
                    "disease_id": c.disease_id,
                    "source_type": c.source_type,
                    "source_name": c.source_name,
                    "url": c.url,
                    "resolved_url": getattr(c, "resolved_url", None) or c.url,
                    "title": c.title,
                    "license": c.license,
                    "review_status": c.review_status,
                    "raw_excerpt": c.raw_excerpt,
                    "content_text": getattr(c, "content_text", None),
                    "content_sections": getattr(c, "content_sections", None) or [],
                }
                for idx, c in enumerate(candidates)
            ]
            if _has_approved_public_sources(source_dicts):
                briefs = await asyncio.gather(
                    _generate_brief(generator, disease=disease, sources=source_dicts, language="en"),
                    _generate_brief(generator, disease=disease, sources=source_dicts, language="zh"),
                )
            else:
                fallback_reason = _catalogue_fallback_reason(source_dicts)
                briefs = [
                    build_catalogue_disease_brief_payload(disease, "en", fallback_reason=fallback_reason),
                    build_catalogue_disease_brief_payload(disease, "zh", fallback_reason=fallback_reason),
                ]
            return {
                "disease_id": disease["disease_id"],
                "fetched_sources": len(candidates),
                "brief_statuses": {brief["language"]: brief["status"] for brief in briefs},
                "brief_previews": {
                    brief["language"]: {
                        "brief": brief.get("brief"),
                        "definition": brief.get("definition"),
                        "clinical_features": brief.get("clinical_features"),
                        "epidemiology": brief.get("epidemiology"),
                        "transmission": brief.get("transmission"),
                        "prevention": brief.get("prevention"),
                        "surveillance_note": brief.get("surveillance_note"),
                        "risk_groups": brief.get("risk_groups"),
                        "model": brief.get("model"),
                    }
                    for brief in briefs
                },
                "sources": source_dicts,
            }

        async with get_database() as db:
            existing = await _existing_sources(db, disease["disease_id"])
            candidates = []
            if force or not existing:
                candidates = await asyncio.to_thread(self.fetcher.fetch, disease, enabled_sources=enabled_sources)
                for candidate in candidates:
                    await _upsert_source(db, candidate)
                if force:
                    await _mark_stale_sources(db, disease["disease_id"], candidates, enabled_sources)
                await db.commit()
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title="Source Fetch Completed",
                    content=(
                        f"Fetched {len(candidates)} candidate source(s) for {disease['disease_id']} "
                        f"from {', '.join(enabled_sources)}"
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "fetched_sources": len(candidates),
                        "source_groups": enabled_sources,
                        "workflow_stage": "source_fetch_completed",
                    },
                )
            else:
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title="Source Reuse",
                    content=(
                        f"Reusing {len(existing)} existing source row(s) for {disease['disease_id']} "
                        "because force refresh is disabled."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "reused_sources": len(existing),
                        "workflow_stage": "source_reuse",
                    },
                )

            await _raise_if_cancelled(task_uuid)
            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 40)

            source_rows = await _existing_sources(db, disease["disease_id"])
            source_dicts = [source_to_dict(row) for row in source_rows]
            brief_rows = []
            fallback_reason = _catalogue_fallback_reason(source_dicts)
            has_public_sources = _has_approved_public_sources(source_dicts)

            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 55)

            brief_languages = ("en", "zh")
            for language in brief_languages:
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title=f"Generating {language.upper()} Brief",
                    content=(
                        f"Generating {language.upper()} disease brief for {disease['disease_id']} "
                        f"using {len(source_dicts)} grounded source row(s)."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "language": language,
                        "source_count": len(source_dicts),
                        "workflow_stage": f"brief_generation_{language}",
                    },
                )

            if has_public_sources:
                generated_results = await asyncio.gather(
                    *[
                        _generate_brief_result(generator, disease=disease, sources=source_dicts, language=language)
                        for language in brief_languages
                    ]
                )
            else:
                generated_results = [
                    {
                        "payload": build_catalogue_disease_brief_payload(
                            disease,
                            language,
                            fallback_reason=fallback_reason,
                        ),
                        "trace": {
                            "generator": "catalogue_fallback",
                            "language": language,
                            "preferred_models": [],
                            "shard_index": 0,
                            "shard_key": f"{str(disease.get('disease_id') or '').strip().upper()}:{language}",
                            "model": None,
                            "provider": None,
                            "token_usage": {},
                            "duration": 0.0,
                            "prompt": None,
                            "system_prompt": None,
                            "response": None,
                            "error": None,
                            "cache_hit": False,
                        },
                    }
                    for language in brief_languages
                ]

            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 80)

            for result in generated_results:
                await _raise_if_cancelled(task_uuid)
                payload = result["payload"]
                trace = result.get("trace") or {}
                language = str(payload.get("language") or trace.get("language") or "unknown").strip().lower()
                model_used = trace.get("model") if isinstance(trace.get("model"), str) else None
                provider_used = trace.get("provider") if isinstance(trace.get("provider"), str) else None
                token_usage = trace.get("token_usage") if isinstance(trace.get("token_usage"), dict) else {}
                tokens_used = _total_tokens_from_usage(token_usage)
                duration = float(trace.get("duration") or 0.0) if trace.get("duration") is not None else None
                error_message = trace.get("error") if isinstance(trace.get("error"), str) else None
                brief_rows.append(await _upsert_brief(db, payload))

                await _log_task(
                    task_uuid,
                    entry_type="success" if error_message is None else "warning",
                    title=f"{language.upper()} Brief Generated",
                    content=(
                        f"{language.upper()} brief status: {payload.get('status') or 'unknown'}\n"
                        f"Model: {model_used or '-'}\n"
                        f"Provider: {provider_used or '-'}\n"
                        f"Preferred shard models: {', '.join(trace.get('preferred_models') or []) or '-'}\n"
                        f"Cache hit: {'yes' if trace.get('cache_hit') else 'no'}"
                    ),
                    prompt=trace.get("prompt") if isinstance(trace.get("prompt"), str) else None,
                    response=trace.get("response") if isinstance(trace.get("response"), str) else None,
                    model_used=model_used,
                    tokens_used=tokens_used,
                    duration=duration,
                    metadata={
                        "disease_id": disease["disease_id"],
                        "language": language,
                        "workflow_stage": f"brief_generation_{language}",
                        "provider": provider_used,
                        "generator": trace.get("generator"),
                        "preferred_models": trace.get("preferred_models") or [],
                        "shard_index": trace.get("shard_index"),
                        "shard_key": trace.get("shard_key"),
                        "token_usage": token_usage,
                        "cache_hit": bool(trace.get("cache_hit")),
                        "status": payload.get("status"),
                        "quality_score": payload.get("quality_score"),
                        "source_count": len(source_dicts),
                        "error": error_message,
                    },
                    success=error_message is None,
                )

            await _log_task(
                task_uuid,
                entry_type="info",
                title="Brief Persistence Completed",
                content=(
                    f"Persisted {len(brief_rows)} brief row(s) for {disease['disease_id']}."
                ),
                metadata={
                    "disease_id": disease["disease_id"],
                    "brief_count": len(brief_rows),
                    "workflow_stage": "brief_persisted",
                },
            )

            await db.commit()

        brief_statuses = {brief.language: brief.status for brief in brief_rows}
        published_languages = [
            brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "published"
        ]
        fallback_languages = [
            brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "fallback"
        ]
        await _log_task(
            task_uuid,
            entry_type="success",
            title="Knowledge Update Completed",
            content=(
                f"{disease['disease_id']} completed with {len(source_dicts)} source row(s) "
                f"and briefs: {brief_statuses}."
            ),
            metadata={
                "disease_id": disease["disease_id"],
                "source_count": len(source_dicts),
                "brief_statuses": brief_statuses,
                "workflow_stage": "knowledge_complete",
            },
        )
        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 100)

        return {
            "disease_id": disease["disease_id"],
            "fetched_sources": len(candidates),
            "total_sources": len(source_dicts),
            "brief_statuses": brief_statuses,
            "published_languages": published_languages,
            "fallback_languages": fallback_languages,
        }

    async def execute_task(self, task: Task) -> dict[str, Any]:
        """Execute a task created by the dashboard or CLI."""
        inp = dict(task.input_data or {})
        disease_id = str(inp.get("disease_id") or inp.get("disease") or "").strip()
        if not disease_id:
            disease_ids = inp.get("disease_ids") or []
            if isinstance(disease_ids, list) and len(disease_ids) == 1:
                disease_id = str(disease_ids[0]).strip()
        if not disease_id:
            raise ValueError("Knowledge task is missing disease_id")

        source_groups = inp.get("source_groups") or inp.get("source") or []
        if isinstance(source_groups, str):
            source_groups = [source_groups]
        force = bool(inp.get("force", False))
        generator_mode = str(inp.get("generator", "ai"))

        return await self.update_disease(
            disease_id,
            enabled_sources=list(source_groups) if isinstance(source_groups, list) else [],
            force=force,
            generator_mode=generator_mode,
            dry_run=bool(inp.get("dry_run", False)),
            task_uuid=task.task_uuid,
        )


async def render_knowledge_preview(
    disease_id: str,
    *,
    enabled_sources: list[str] | None = None,
    generator_mode: str = "ai",
) -> dict[str, Any]:
    """Convenience wrapper used by the CLI dry-run mode."""
    service = DiseaseKnowledgeUpdateService()
    return await service.update_disease(
        disease_id,
        enabled_sources=enabled_sources,
        force=True,
        generator_mode=generator_mode,
        dry_run=True,
    )
