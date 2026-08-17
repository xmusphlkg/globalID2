"""Evidence-grounded model enrichment for Research Radar literature records.

Model output is never published directly.  The generator is grounded in one
article's internal metadata/abstract, rejects long verbatim overlap, stores a
compact provenance trace, and writes summaries through the autopilot quality
gate. Borderline output remains in ``review`` status as an exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select

from src.ai.agents.base import BaseAgent
from src.core import get_db, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureSummary,
    LiteratureTopicLink,
    StandardDisease,
    Task,
)


logger = get_logger(__name__)
SUMMARY_FIELDS = (
    "research_question",
    "study_design",
    "population_setting",
    "main_findings",
    "public_health_relevance",
    "limitations",
    "gids_interpretation",
)
ALLOWED_EVIDENCE = {"title", "abstract", "bibliographic_metadata", "classifier_links"}


class LiteratureEvidenceAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            name="literature_evidence_summarizer",
            temperature=0.1,
            max_tokens=1800,
        )

    async def process(self, **kwargs: Any) -> dict[str, Any]:
        response = await self.complete(
            prompt=kwargs["prompt"],
            system=kwargs["system"],
            use_cache=True,
            preferred_models=kwargs.get("preferred_models"),
            wait_for_model_recovery=False,
            max_attempts_per_model=1,
            max_quota_recovery_rounds=0,
            model_request_timeout_seconds=kwargs.get("timeout_seconds"),
        )
        return {"raw_response": response}


@dataclass(slots=True)
class EnrichmentResult:
    fields: dict[str, str | None]
    evidence_map: dict[str, dict[str, Any]]
    quality_score: float
    review_notes: str
    model: str | None
    provider: str | None
    token_usage: dict[str, Any]
    source_fingerprint: str


def source_fingerprint(article: LiteratureArticle) -> str:
    payload = "\n".join(
        str(value or "")
        for value in (
            article.title,
            article.doi,
            article.journal,
            article.published_at.isoformat() if article.published_at else None,
            article.abstract_text,
            article.integrity_status,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_json(value: str) -> dict[str, Any]:
    text = value.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Literature enrichment response must be a JSON object")
    return parsed


def _has_long_verbatim_overlap(source: str, output: str, *, words: int = 12) -> bool:
    source_words = re.findall(r"[a-z0-9]+", source.lower())
    output_words = re.findall(r"[a-z0-9]+", output.lower())
    if len(source_words) < words or len(output_words) < words:
        return False
    source_ngrams = {tuple(source_words[index : index + words]) for index in range(len(source_words) - words + 1)}
    return any(
        tuple(output_words[index : index + words]) in source_ngrams
        for index in range(len(output_words) - words + 1)
    )


class LiteratureSummaryGenerator:
    PROTOCOL_VERSION = 1

    def __init__(self, agent: LiteratureEvidenceAgent | None = None) -> None:
        self.agent = agent or LiteratureEvidenceAgent()

    async def generate(
        self,
        *,
        article: LiteratureArticle,
        language: str,
        diseases: list[str],
        countries: list[str],
        topics: list[str],
        timeout_seconds: int,
        preferred_models: list[str],
    ) -> EnrichmentResult:
        language = "zh" if language == "zh" else "en"
        evidence = {
            "title": article.title,
            "abstract": article.abstract_text,
            "bibliographic_metadata": {
                "doi": article.doi,
                "journal": article.journal,
                "publisher": article.publisher,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "article_type": article.article_type,
                "study_type": article.study_type,
                "peer_review_status": article.peer_review_status,
                "integrity_status": article.integrity_status,
            },
            "classifier_links": {
                "diseases": diseases,
                "countries": countries,
                "topics": topics,
            },
        }
        system = (
            f"GIDS literature evidence protocol {self.PROTOCOL_VERSION}. Use only the supplied single-article evidence. "
            "Do not use outside knowledge, infer causality, provide clinical advice, or claim that surveillance data "
            "confirms the paper. Paraphrase; never reproduce a sentence or a long phrase from the abstract. If evidence "
            "does not support a field, return null. The output language is "
            f"{'Simplified Chinese' if language == 'zh' else 'English'}. Return JSON only."
        )
        schema = {
            field: {
                "text": "concise evidence-grounded prose or null",
                "evidence": ["title", "abstract", "bibliographic_metadata", "classifier_links"],
                "confidence": "number from 0 to 1",
            }
            for field in SUMMARY_FIELDS
        }
        prompt = json.dumps(
            {
                "task": "Create a conservative structured evidence summary for editorial review.",
                "requirements": {
                    "main_findings": "Report findings only when explicitly present in the abstract.",
                    "limitations": "Include only limitations explicitly stated or directly evident from the supplied study design; otherwise null.",
                    "gids_interpretation": "Explain discoverability/context only; do not connect the paper to a live surveillance signal.",
                    "field_length": "Prefer 1-3 short sentences per non-null field.",
                },
                "output_schema": schema,
                "evidence": evidence,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        response = await self.agent.process(
            prompt=prompt,
            system=system,
            preferred_models=preferred_models,
            timeout_seconds=timeout_seconds,
        )
        parsed = _parse_json(str(response["raw_response"]))
        fields: dict[str, str | None] = {}
        evidence_map: dict[str, dict[str, Any]] = {}
        rejected_overlap: list[str] = []
        confidences: list[float] = []
        for field in SUMMARY_FIELDS:
            raw = parsed.get(field)
            if raw is None:
                fields[field] = None
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"Literature enrichment field '{field}' must be an object or null")
            text = str(raw.get("text") or "").strip() or None
            evidence_sources = [
                value for value in raw.get("evidence") or []
                if isinstance(value, str) and value in ALLOWED_EVIDENCE
            ]
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            if text and _has_long_verbatim_overlap(article.abstract_text or "", text):
                rejected_overlap.append(field)
                text = None
                confidence = 0.0
            if text and not evidence_sources:
                text = None
                confidence = 0.0
            fields[field] = text
            if text:
                confidences.append(confidence)
                evidence_map[field] = {"sources": evidence_sources, "confidence": round(confidence, 3)}

        coverage = sum(value is not None for value in fields.values()) / len(SUMMARY_FIELDS)
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        quality_score = round((0.55 * coverage) + (0.45 * mean_confidence), 3)
        conversation = self.agent.get_latest_conversation() or {}
        notes = "Generated from one article for editorial review; not public until approved."
        if rejected_overlap:
            notes += f" Removed verbatim-overlap fields: {', '.join(rejected_overlap)}."
        return EnrichmentResult(
            fields=fields,
            evidence_map=evidence_map,
            quality_score=quality_score,
            review_notes=notes,
            model=str(conversation.get("model") or "") or None,
            provider=str(conversation.get("provider") or "") or None,
            token_usage=conversation.get("tokens") if isinstance(conversation.get("tokens"), dict) else {},
            source_fingerprint=source_fingerprint(article),
        )


class LiteratureEnrichmentPipeline:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def execute(self, task: Task) -> dict[str, Any]:
        if not self.config.ai_enrichment_enabled:
            raise RuntimeError("Literature AI enrichment is disabled by configuration")
        input_data = task.input_data or {}
        requested_ids = [str(value) for value in input_data.get("article_ids") or [] if value]
        force = bool(input_data.get("force", False))
        languages = [
            value for value in input_data.get("languages") or self.config.ai_enrichment_languages
            if value in {"en", "zh"}
        ]
        limit = min(
            max(1, int(input_data.get("limit") or self.config.ai_enrichment_batch_size)),
            self.config.ai_enrichment_batch_size,
        )
        articles, context = await self._load_articles(
            requested_ids=requested_ids,
            limit=limit,
            languages=languages,
            force=force,
        )
        generator = LiteratureSummaryGenerator()
        counts = {"articles": len(articles), "generated": 0, "skipped": 0, "failed": 0}
        errors: list[dict[str, str]] = []
        total = max(1, len(articles) * max(1, len(languages)))
        step = 0
        for article in articles:
            for language in languages:
                step += 1
                if await task_manager.is_cancel_requested(task.task_uuid):
                    raise RuntimeError("Literature enrichment cancelled")
                try:
                    if await self._should_skip(article, language=language, force=force):
                        counts["skipped"] += 1
                        continue
                    result = await generator.generate(
                        article=article,
                        language=language,
                        diseases=context[article.article_id]["diseases"],
                        countries=context[article.article_id]["countries"],
                        topics=context[article.article_id]["topics"],
                        timeout_seconds=self.config.ai_model_request_timeout_seconds,
                        preferred_models=list(input_data.get("preferred_models") or []),
                    )
                    await self._store(article.article_id, language=language, result=result)
                    counts["generated"] += 1
                except Exception as exc:
                    logger.warning("Literature enrichment failed for {}/{}: {}", article.article_id, language, exc)
                    counts["failed"] += 1
                    errors.append({"article_id": article.article_id, "language": language, "error": str(exc)[:500]})
                finally:
                    await task_manager.update_task_progress(task.task_uuid, min(99, int(100 * step / total)))
        await task_manager.update_task_progress(task.task_uuid, 100)
        automation = None
        if self.config.autopilot_enabled:
            from src.services.literature_automation_service import literature_automation_service

            automation = await literature_automation_service.reconcile()
        return {**counts, "languages": languages, "errors": errors[:20], "automation": automation}

    async def _load_articles(
        self,
        *,
        requested_ids: list[str],
        limit: int,
        languages: list[str],
        force: bool,
    ) -> tuple[list[LiteratureArticle], dict[str, dict[str, list[str]]]]:
        async with get_db() as db:
            query = (
                select(LiteratureArticle)
                .where(
                    LiteratureArticle.publication_status.in_(("review", "published")),
                    LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                    LiteratureArticle.abstract_text.is_not(None),
                )
                .order_by(LiteratureArticle.discovery_score.desc(), LiteratureArticle.indexed_at.desc())
                # Pull beyond one batch so completed top-ranked records do not
                # starve lower-ranked records in scheduled operation.
                .limit(limit if requested_ids else max(100, limit * 20))
            )
            if self.config.ai_require_open_access:
                query = query.where(LiteratureArticle.open_access_status == "open")
            if requested_ids:
                query = query.where(LiteratureArticle.article_id.in_(requested_ids))
            candidates = list((await db.execute(query)).scalars().all())
            candidate_ids = [article.article_id for article in candidates]
            existing_summaries = (
                await db.execute(
                    select(LiteratureSummary).where(LiteratureSummary.article_id.in_(candidate_ids))
                )
            ).scalars().all() if candidate_ids else []
            summaries_by_key = {
                (summary.article_id, summary.language): summary
                for summary in existing_summaries
            }

            def needs_work(article: LiteratureArticle) -> bool:
                if len(article.abstract_text or "") < self.config.ai_min_abstract_characters:
                    return False
                fingerprint = source_fingerprint(article)
                for language in languages:
                    summary = summaries_by_key.get((article.article_id, language))
                    if summary is None:
                        return True
                    if summary.generated_by == "control-plane-editor" or (
                        summary.generation_metadata or {}
                    ).get("editorial_reviewed_at"):
                        continue
                    previous = str((summary.generation_metadata or {}).get("source_fingerprint") or "")
                    auto_published = bool((summary.generation_metadata or {}).get("autopilot"))
                    attempts = int((summary.generation_metadata or {}).get("quality_attempts") or 0)
                    if force or previous != fingerprint or (
                        summary.status == "review"
                        and self.config.autopilot_enabled
                        and attempts < self.config.ai_max_quality_attempts
                    ) or (
                        summary.status == "published" and auto_published and previous != fingerprint
                    ):
                        return True
                return False

            articles = [article for article in candidates if needs_work(article)][:limit]
            article_ids = [article.article_id for article in articles]
            context = {
                article_id: {"diseases": [], "countries": [], "topics": []}
                for article_id in article_ids
            }
            if article_ids:
                disease_rows = (
                    await db.execute(
                        select(LiteratureDiseaseLink, StandardDisease)
                        .join(StandardDisease, StandardDisease.disease_id == LiteratureDiseaseLink.disease_id)
                        .where(LiteratureDiseaseLink.article_id.in_(article_ids))
                    )
                ).all()
                country_rows = (
                    await db.execute(select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids)))
                ).scalars().all()
                topic_rows = (
                    await db.execute(select(LiteratureTopicLink).where(LiteratureTopicLink.article_id.in_(article_ids)))
                ).scalars().all()
                for link, disease in disease_rows:
                    context[link.article_id]["diseases"].append(disease.standard_name_en)
                for link in country_rows:
                    context[link.article_id]["countries"].append(link.country_name)
                for link in topic_rows:
                    context[link.article_id]["topics"].append(link.topic)
        return list(articles), context

    async def _should_skip(self, article: LiteratureArticle, *, language: str, force: bool) -> bool:
        if len(article.abstract_text or "") < self.config.ai_min_abstract_characters:
            return True
        async with get_db() as db:
            existing = (
                await db.execute(
                    select(LiteratureSummary).where(
                        LiteratureSummary.article_id == article.article_id,
                        LiteratureSummary.language == language,
                    )
                )
            ).scalar_one_or_none()
        if existing is None:
            return False
        if existing.generated_by == "control-plane-editor" or (
            existing.generation_metadata or {}
        ).get("editorial_reviewed_at"):
            return True
        fingerprint = str((existing.generation_metadata or {}).get("source_fingerprint") or "")
        auto_published = bool((existing.generation_metadata or {}).get("autopilot"))
        if existing.status == "published" and not auto_published:
            return True
        if force or fingerprint != source_fingerprint(article):
            return False
        attempts = int((existing.generation_metadata or {}).get("quality_attempts") or 0)
        return not (
            self.config.autopilot_enabled
            and existing.status == "review"
            and attempts < self.config.ai_max_quality_attempts
        )

    async def _store(self, article_id: str, *, language: str, result: EnrichmentResult) -> None:
        async with get_db() as db:
            summary = (
                await db.execute(
                    select(LiteratureSummary).where(
                        LiteratureSummary.article_id == article_id,
                        LiteratureSummary.language == language,
                    )
                )
            ).scalar_one_or_none()
            if summary is None:
                summary = LiteratureSummary(article_id=article_id, language=language)
                db.add(summary)
            existing_metadata = dict(summary.generation_metadata or {})
            if summary.generated_by == "control-plane-editor" or existing_metadata.get("editorial_reviewed_at"):
                return
            if summary.status == "published" and not existing_metadata.get("autopilot"):
                return
            for field, value in result.fields.items():
                setattr(summary, field, value)
            summary.status = "review"
            summary.generated_by = "literature-evidence-agent"
            summary.model = result.model
            summary.provider = result.provider
            summary.quality_score = result.quality_score
            summary.evidence_map = result.evidence_map
            summary.generation_metadata = {
                "protocol_version": LiteratureSummaryGenerator.PROTOCOL_VERSION,
                "source_fingerprint": result.source_fingerprint,
                "token_usage": result.token_usage,
                "publication_gate": "autopilot-quality-gate" if self.config.autopilot_enabled else "human-review-required",
                "quality_attempts": int(existing_metadata.get("quality_attempts") or 0) + 1,
            }
            summary.generated_at = datetime.now(timezone.utc)
            summary.review_notes = result.review_notes
            await db.commit()


__all__ = [
    "EnrichmentResult",
    "LiteratureEnrichmentPipeline",
    "LiteratureEvidenceAgent",
    "LiteratureSummaryGenerator",
    "SUMMARY_FIELDS",
    "source_fingerprint",
]
