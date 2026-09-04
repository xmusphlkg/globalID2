"""Evidence-grounded model enrichment for Research Radar literature records.

Model output is never published directly.  The generator is grounded in one
article's internal metadata/abstract, rejects long verbatim overlap, stores a
compact provenance trace, and writes summaries through the autopilot quality
gate. Borderline output remains in ``review`` status as an exception.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select

from src.ai.agents.base import BaseAgent
from src.core import get_config, get_db, get_logger
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
        literature_config = get_config().literature
        response = await self.complete(
            prompt=kwargs["prompt"],
            system=kwargs["system"],
            use_cache=True,
            preferred_models=kwargs.get("preferred_models"),
            wait_for_model_recovery=bool(
                kwargs.get(
                    "wait_for_model_recovery",
                    literature_config.ai_wait_for_model_recovery,
                )
            ),
            max_attempts_per_model=1,
            max_quota_recovery_rounds=int(
                kwargs.get(
                    "max_quota_recovery_rounds",
                    literature_config.ai_quota_recovery_rounds,
                )
            ),
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
    canonical_summary_fingerprint: str | None = None


def canonical_summary_fingerprint(fields: dict[str, str | None]) -> str:
    """Fingerprint the English semantic contract used for a translation."""
    canonical = {field: fields.get(field) for field in SUMMARY_FIELDS}
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    PROTOCOL_VERSION = 2
    BILINGUAL_PROTOCOL_VERSION = "canonical-en-translation.v1"

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
        canonical_fields: dict[str, str | None] | None = None,
    ) -> EnrichmentResult:
        language = "zh" if language == "zh" else "en"
        if language == "zh" and not canonical_fields:
            raise ValueError("canonical_english_summary_required")
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
            "does not support a field, return null, except limitations: when explicit limitations are absent, write a "
            "source-scope limitation about reliance on the supplied single-article abstract/metadata. The output language is "
            f"{'Simplified Chinese' if language == 'zh' else 'English'}. Return JSON only."
        )
        if language == "zh":
            system += (
                " The supplied canonical English summary is the semantic contract. Translate each field "
                "faithfully: preserve its factual claims, qualifications, comparisons, and null fields; "
                "do not add, remove, strengthen, or weaken any claim. If the canonical limitations field is null, "
                "use the same source-scope limitation policy instead of leaving limitations null."
            )
        schema = {
            field: {
                "text": "concise evidence-grounded prose or null",
                "evidence": ["title", "abstract", "bibliographic_metadata", "classifier_links"],
                "confidence": "number from 0 to 1",
            }
            for field in SUMMARY_FIELDS
        }
        request = {
                "task": (
                    "Create a faithful field-aligned Simplified Chinese rendering for editorial review."
                    if language == "zh"
                    else "Create a conservative structured evidence summary for editorial review."
                ),
                "requirements": {
                    "main_findings": "Report findings only when explicitly present in the abstract.",
                    "limitations": (
                        "Prefer limitations explicitly stated or directly evident from the supplied study design. "
                        "If none are available, state that the summary is limited to the supplied single-article "
                        "abstract/metadata and requires the original paper for decision-grade interpretation."
                    ),
                    "gids_interpretation": "Explain discoverability/context only; do not connect the paper to a live surveillance signal.",
                    "field_length": "Prefer 1-3 short sentences per non-null field.",
                },
                "output_schema": schema,
                "evidence": evidence,
            }
        if language == "zh":
            request["canonical_summary_en"] = {
                field: canonical_fields.get(field) for field in SUMMARY_FIELDS
            }
        prompt = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        response = await self.agent.process(
            prompt=prompt,
            system=system,
            preferred_models=preferred_models,
            timeout_seconds=timeout_seconds,
        )
        parse_failed = False
        try:
            parsed = _parse_json(str(response["raw_response"]))
        except Exception:
            if language != "zh" or not canonical_fields:
                raise
            parse_failed = True
            parsed = self._canonical_contract_fallback_summary(canonical_fields)
        if language == "zh" and canonical_fields:
            parsed = await self._repair_bilingual_topology(
                parsed,
                canonical_fields=canonical_fields,
                system=system,
                schema=schema,
                evidence=evidence,
                preferred_models=preferred_models,
                timeout_seconds=timeout_seconds,
            )
        fields: dict[str, str | None] = {}
        evidence_map: dict[str, dict[str, Any]] = {}
        rejected_overlap: list[str] = []
        confidences: list[float] = []
        for field in SUMMARY_FIELDS:
            raw = parsed.get(field)
            if raw is None and field == "limitations":
                fields[field] = self._source_scope_limitations(language)
                confidences.append(0.72)
                evidence_map[field] = {
                    "sources": ["abstract", "bibliographic_metadata"],
                    "confidence": 0.72,
                    "fallback": "source_scope_limitations",
                }
                continue
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
            fallback_reason = str(raw.get("fallback") or "").strip() or None
            if text and _has_long_verbatim_overlap(article.abstract_text or "", text):
                rejected_overlap.append(field)
                text = None
                confidence = 0.0
            if text and not evidence_sources:
                text = None
                confidence = 0.0
            if (
                not text
                and language == "zh"
                and canonical_fields
                and canonical_fields.get(field) not in (None, "")
            ):
                fallback = self._canonical_contract_fallback(field, canonical_fields.get(field))
                text = str(fallback["text"])
                evidence_sources = list(fallback["evidence"])
                confidence = float(fallback["confidence"])
                fallback_reason = "canonical_english_contract"
            fields[field] = text
            if text:
                confidences.append(confidence)
                evidence_map[field] = {"sources": evidence_sources, "confidence": round(confidence, 3)}
                if fallback_reason:
                    evidence_map[field]["fallback"] = fallback_reason

        coverage = sum(value is not None for value in fields.values()) / len(SUMMARY_FIELDS)
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        quality_score = round((0.55 * coverage) + (0.45 * mean_confidence), 3)
        conversation = self.agent.get_latest_conversation() or {}
        notes = "Generated from one article for editorial review; not public until approved."
        if rejected_overlap:
            notes += f" Removed verbatim-overlap fields: {', '.join(rejected_overlap)}."
        if parse_failed:
            notes += " Recovered malformed Chinese JSON from the canonical English contract."
        return EnrichmentResult(
            fields=fields,
            evidence_map=evidence_map,
            quality_score=quality_score,
            review_notes=notes,
            model=str(conversation.get("model") or "") or None,
            provider=str(conversation.get("provider") or "") or None,
            token_usage=conversation.get("tokens") if isinstance(conversation.get("tokens"), dict) else {},
            source_fingerprint=source_fingerprint(article),
            canonical_summary_fingerprint=(
                canonical_summary_fingerprint(canonical_fields)
                if language == "zh" and canonical_fields else None
            ),
        )

    async def _repair_bilingual_topology(
        self,
        parsed: dict[str, Any],
        *,
        canonical_fields: dict[str, str | None],
        system: str,
        schema: dict[str, dict[str, Any]],
        evidence: dict[str, Any],
        preferred_models: list[str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        missing = [
            field for field in SUMMARY_FIELDS
            if canonical_fields.get(field) not in (None, "")
            and self._raw_field_is_null(parsed.get(field))
        ]
        for field in SUMMARY_FIELDS:
            if canonical_fields.get(field) in (None, "") and not self._raw_field_is_null(parsed.get(field)):
                parsed[field] = None
        if missing:
            repair_request = {
                "task": "Repair a Simplified Chinese field-aligned summary response.",
                "requirements": {
                    "repair_only_fields": missing,
                    "semantic_contract": "Each repaired field must preserve the corresponding canonical English field.",
                    "return_schema": "Return JSON containing only repaired fields as objects with text, evidence, and confidence.",
                },
                "canonical_summary_en": {
                    field: canonical_fields.get(field) for field in missing
                },
                "previous_response": {
                    field: parsed.get(field) for field in SUMMARY_FIELDS
                },
                "output_schema": {field: schema[field] for field in missing},
                "evidence": evidence,
            }
            try:
                response = await self.agent.process(
                    prompt=json.dumps(repair_request, ensure_ascii=False, separators=(",", ":")),
                    system=(
                        system
                        + " Repair mode: return non-null Simplified Chinese objects for every requested field. "
                        "Use the canonical English field as the semantic contract."
                    ),
                    preferred_models=preferred_models,
                    timeout_seconds=timeout_seconds,
                )
                repaired = _parse_json(str(response["raw_response"]))
                for field in missing:
                    if isinstance(repaired.get(field), dict) and not self._raw_field_is_null(repaired.get(field)):
                        parsed[field] = repaired[field]
            except Exception as exc:
                logger.warning("Literature zh topology repair failed: {}", exc)
        for field in missing:
            if self._raw_field_is_null(parsed.get(field)):
                parsed[field] = self._canonical_contract_fallback(field, canonical_fields.get(field))
        return parsed

    @staticmethod
    def _raw_field_is_null(raw: Any) -> bool:
        if raw is None:
            return True
        if not isinstance(raw, dict):
            return False
        return not str(raw.get("text") or "").strip()

    @staticmethod
    def _canonical_contract_fallback(field: str, canonical_text: str | None) -> dict[str, Any]:
        label = {
            "research_question": "研究问题",
            "study_design": "研究设计",
            "population_setting": "人群与场景",
            "main_findings": "主要发现",
            "public_health_relevance": "公共卫生意义",
            "limitations": "局限性",
            "gids_interpretation": "GIDS 解读",
        }.get(field, "摘要字段")
        return {
            "text": f"{label}按英文规范摘要保留同等含义：{canonical_text}",
            "evidence": ["abstract", "bibliographic_metadata"],
            "confidence": 0.55,
            "fallback": "canonical_english_contract",
        }

    def _canonical_contract_fallback_summary(
        self,
        canonical_fields: dict[str, str | None],
    ) -> dict[str, Any]:
        return {
            field: (
                None
                if canonical_fields.get(field) in (None, "")
                else self._canonical_contract_fallback(field, canonical_fields.get(field))
            )
            for field in SUMMARY_FIELDS
        }

    @staticmethod
    def _source_scope_limitations(language: str) -> str:
        if language == "zh":
            return (
                "可用证据仅限于该单篇文献的题录、摘要和分类链接；用于决策级解释前，"
                "仍需核对原文全文、研究方法细节和作者声明的局限。"
            )
        return (
            "The available evidence is limited to this single article's bibliographic metadata, abstract, "
            "and classifier links; decision-grade interpretation still requires checking the full paper, "
            "methods detail, and author-stated limitations."
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
        languages = list(dict.fromkeys([
            value for value in input_data.get("languages") or self.config.ai_enrichment_languages
            if value in {"en", "zh"}
        ]))
        if "en" in languages and "zh" in languages:
            languages = ["en", "zh"]
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
        counts = {"articles": len(articles), "generated": 0, "skipped": 0, "failed": 0}
        errors: list[dict[str, str]] = []
        total = max(1, len(articles) * max(1, len(languages)))
        step = 0
        progress_lock = asyncio.Lock()
        counts_lock = asyncio.Lock()
        concurrency = min(
            max(1, int(getattr(self.config, "ai_enrichment_concurrency", 1))),
            max(1, len(articles)),
        )
        semaphore = asyncio.Semaphore(concurrency)
        preferred_models = list(input_data.get("preferred_models") or [])

        async def record_progress() -> None:
            nonlocal step
            async with progress_lock:
                step += 1
                await task_manager.update_task_progress(task.task_uuid, min(99, int(100 * step / total)))

        async def process_article(article: LiteratureArticle) -> None:
            generator = LiteratureSummaryGenerator()
            canonical_fields = context[article.article_id].get("canonical_en")
            async with semaphore:
                for language in languages:
                    if await task_manager.is_cancel_requested(task.task_uuid):
                        raise RuntimeError("Literature enrichment cancelled")
                    try:
                        if await self._should_skip(article, language=language, force=force):
                            async with counts_lock:
                                counts["skipped"] += 1
                            continue
                        result = await generator.generate(
                            article=article,
                            language=language,
                            diseases=context[article.article_id]["diseases"],
                            countries=context[article.article_id]["countries"],
                            topics=context[article.article_id]["topics"],
                            timeout_seconds=self.config.ai_model_request_timeout_seconds,
                            preferred_models=preferred_models,
                            canonical_fields=canonical_fields if language == "zh" else None,
                        )
                        await self._store(article.article_id, language=language, result=result)
                        if language == "en":
                            canonical_fields = dict(result.fields)
                        async with counts_lock:
                            counts["generated"] += 1
                    except Exception as exc:
                        if language == "en":
                            canonical_fields = None
                        logger.warning("Literature enrichment failed for {}/{}: {}", article.article_id, language, exc)
                        await self._store_failure(article, language=language, error=exc)
                        async with counts_lock:
                            counts["failed"] += 1
                            errors.append({"article_id": article.article_id, "language": language, "error": str(exc)[:500]})
                    finally:
                        await record_progress()

        await asyncio.gather(*(process_article(article) for article in articles))
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
            base_query = (
                select(LiteratureArticle)
                .where(
                    LiteratureArticle.publication_status.in_(("review", "published")),
                    LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                    LiteratureArticle.abstract_text.is_not(None),
                )
                .order_by(LiteratureArticle.discovery_score.desc(), LiteratureArticle.indexed_at.desc())
            )
            if self.config.ai_require_open_access:
                base_query = base_query.where(LiteratureArticle.open_access_status == "open")
            if requested_ids:
                base_query = base_query.where(LiteratureArticle.article_id.in_(requested_ids))
            summaries_by_key: dict[tuple[str, str], LiteratureSummary] = {}

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

            articles: list[LiteratureArticle] = []
            scan_batch_size = limit if requested_ids else max(100, limit * 20)
            max_scan = (
                limit
                if requested_ids
                else max(scan_batch_size, int(getattr(self.config, "ai_enrichment_candidate_scan_limit", 5000)))
            )
            scanned = 0
            offset = 0
            while len(articles) < limit and scanned < max_scan:
                remaining_scan = max_scan - scanned
                batch_query = base_query.limit(min(scan_batch_size, remaining_scan))
                if not requested_ids:
                    batch_query = batch_query.offset(offset)
                candidates = list((await db.execute(batch_query)).scalars().all())
                if not candidates:
                    break
                scanned += len(candidates)
                offset += len(candidates)
                candidate_ids = [article.article_id for article in candidates]
                existing_summaries = list(
                    (
                        await db.execute(
                            select(LiteratureSummary).where(LiteratureSummary.article_id.in_(candidate_ids))
                        )
                    ).scalars().all()
                ) if candidate_ids else []
                summaries_by_key.update(
                    {
                        (summary.article_id, summary.language): summary
                        for summary in existing_summaries
                    }
                )
                for article in candidates:
                    if needs_work(article):
                        articles.append(article)
                        if len(articles) >= limit:
                            break
                if requested_ids or len(candidates) < scan_batch_size:
                    break
            article_ids = [article.article_id for article in articles]
            selected_summaries = list(
                (
                    await db.execute(
                        select(LiteratureSummary).where(LiteratureSummary.article_id.in_(article_ids))
                    )
                ).scalars().all()
            ) if article_ids else []
            context: dict[str, dict[str, Any]] = {
                article_id: {"diseases": [], "countries": [], "topics": [], "canonical_en": None}
                for article_id in article_ids
            }
            if article_ids:
                for summary in selected_summaries:
                    if summary.article_id in context and summary.language == "en":
                        context[summary.article_id]["canonical_en"] = {
                            field: getattr(summary, field) for field in SUMMARY_FIELDS
                        }
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
            if language == "zh" and result.canonical_summary_fingerprint:
                summary.generation_metadata["bilingual_alignment"] = {
                    "protocol_version": LiteratureSummaryGenerator.BILINGUAL_PROTOCOL_VERSION,
                    "canonical_language": "en",
                    "canonical_summary_fingerprint": result.canonical_summary_fingerprint,
                }
            summary.generated_at = datetime.now(timezone.utc)
            summary.review_notes = result.review_notes
            await db.commit()

    async def _store_failure(self, article: LiteratureArticle, *, language: str, error: Exception) -> None:
        async with get_db() as db:
            summary = (
                await db.execute(
                    select(LiteratureSummary).where(
                        LiteratureSummary.article_id == article.article_id,
                        LiteratureSummary.language == language,
                    )
                )
            ).scalar_one_or_none()
            if summary is None:
                summary = LiteratureSummary(article_id=article.article_id, language=language)
                db.add(summary)
            existing_metadata = dict(summary.generation_metadata or {})
            if summary.generated_by == "control-plane-editor" or existing_metadata.get("editorial_reviewed_at"):
                return
            if summary.status == "published" and not existing_metadata.get("autopilot"):
                return
            attempts = int(existing_metadata.get("quality_attempts") or 0) + 1
            summary.status = "review"
            summary.generated_by = summary.generated_by or "literature-evidence-agent"
            summary.generation_metadata = {
                **existing_metadata,
                "protocol_version": LiteratureSummaryGenerator.PROTOCOL_VERSION,
                "source_fingerprint": source_fingerprint(article),
                "publication_gate": "generation-failed",
                "quality_attempts": attempts,
                "last_generation_error": type(error).__name__,
            }
            summary.review_notes = (
                f"{summary.review_notes or ''} Generation failed for {language}: {str(error)[:240]}"
            ).strip()
            summary.generated_at = datetime.now(timezone.utc)
            await db.commit()


__all__ = [
    "EnrichmentResult",
    "LiteratureEnrichmentPipeline",
    "LiteratureEvidenceAgent",
    "LiteratureSummaryGenerator",
    "SUMMARY_FIELDS",
    "canonical_summary_fingerprint",
    "source_fingerprint",
]
