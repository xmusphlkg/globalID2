"""LLM-backed disease brief generation using the AI model center."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

from src.ai.agents.base import BaseAgent
from src.ai.model_center import get_runtime_routes, record_route_runtime_failure
from src.core import get_config, get_logger
from src.knowledge.brief_generator import DISCLAIMER_EN, DISCLAIMER_ZH
from src.knowledge.citations import (
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.evidence import (
    EvidenceManifest,
    build_evidence_manifest,
    prepare_evidence_packet,
)
from src.knowledge.profile_schema import resolve_knowledge_profile_schema
from src.knowledge.quality import apply_knowledge_quality_gate

logger = get_logger(__name__)


class KnowledgeBriefAgent(BaseAgent):
    """Small concrete agent for schema-first knowledge brief generation."""

    def __init__(self, *, max_tokens: int = 3600) -> None:
        super().__init__(
            name="knowledge_brief_generator",
            temperature=0.2,
            max_tokens=max_tokens,
        )

    async def process(self, **kwargs) -> dict[str, Any]:
        response = await self.complete(
            prompt=kwargs["prompt"],
            system=kwargs["system"],
            use_cache=True,
        )
        return {"raw_response": response}


class AIDiseaseBriefGenerator:
    """Generate source-grounded disease briefs with model-center routing."""

    # v5 adds independent, targeted semantic repair after format and citation
    # recovery. Keep it explicit so task traces distinguish earlier drafts.
    PROMPT_PROTOCOL_VERSION = 5
    PUBLIC_SOURCE_TYPES = {"registry_definition", "who", "who_don", "web_search", "wikidata", "wikipedia", "pubmed"}
    AUTHORITATIVE_SOURCE_TYPES = {"registry_definition", "who", "who_don"}

    def __init__(self, agent: KnowledgeBriefAgent | None = None) -> None:
        self.agent = agent

    async def generate(
        self,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
    ) -> dict[str, Any]:
        result = await self.generate_with_trace(disease=disease, sources=sources, language=language)
        return result["payload"]

    async def generate_with_trace(
        self,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
    ) -> dict[str, Any]:
        language = "zh" if language == "zh" else "en"
        profile_schema = resolve_knowledge_profile_schema(disease)
        target_sections = (
            list(disease.get("target_sections") or [])
            if "target_sections" in disease
            else list(profile_schema.required_fields)
        )
        evidence_target_sections = list(
            disease.get("evidence_target_sections") or target_sections or profile_schema.required_fields
        )
        if disease.get("_evidence_packet_prepared"):
            public_sources = list(sources)
        else:
            public_sources = self._usable_public_sources(
                sources,
                disease=disease,
            )
        scaffold = self._empty_scaffold(
            disease=disease,
            sources=public_sources,
            language=language,
            profile_schema=profile_schema,
            target_sections=target_sections,
        )
        preferred_models, shard_index, shard_key = await self._preferred_models_for(
            disease_id=str(disease.get("disease_id") or ""),
            language=language,
        )
        if not public_sources:
            return {
                "payload": scaffold,
                "trace": {
                    "generator": "ai",
                    "language": language,
                    "preferred_models": preferred_models,
                    "shard_index": shard_index,
                    "shard_key": shard_key,
                    "model": None,
                    "provider": None,
                    "token_usage": {},
                    "duration": 0.0,
                    "prompt": None,
                    "system_prompt": None,
                    "response": None,
                    "error": "No approved public evidence with substantive content",
                    "cache_hit": False,
                },
            }

        source_ids = [src.get("id") for src in public_sources if src.get("id") is not None]
        evidence_manifest = build_evidence_manifest(
            public_sources,
            profile_schema,
            target_sections=evidence_target_sections,
            entity_aliases=disease.get("evidence_entity_aliases") or (),
        )
        system = self._system_prompt(language)
        prompt_payload = self._prompt_payload(
            disease=disease,
            sources=public_sources,
            language=language,
            evidence_manifest=evidence_manifest,
        )
        prompt = self._user_prompt(
            disease=disease,
            sources=public_sources,
            language=language,
            evidence_manifest=evidence_manifest,
        )
        output_token_budget = self._output_token_budget(target_sections)
        agent = self._spawn_agent(max_tokens=output_token_budget)
        started_at = time.time()
        response: str | None = None
        format_repair_prompt: str | None = None
        format_repair_attempted = False
        repair_attempts = max(
            0,
            int(get_config().ai.knowledge_output_repair_attempts),
        )
        # Syntax, citation, and semantic omission are independent failure
        # classes. Sharing one attempt lets an early, recoverable citation
        # repair suppress the later field-completion repair even when the
        # evidence packet explicitly supports the missing field.
        format_repair_budget = repair_attempts
        citation_repair_budget = repair_attempts
        quality_repair_budget = repair_attempts

        try:
            response = await self._complete_with_policy(
                agent,
                prompt=prompt,
                system=system,
                preferred_models=preferred_models,
            )
            latest_conversation = agent.get_latest_conversation() or {}
        except Exception as exc:
            logger.warning(
                "AI disease brief generation failed for {}/{}: {}",
                disease.get("disease_id"),
                language,
                exc,
            )
            scaffold["review_notes"] = f"AI generation failed: {exc}"
            scaffold["metadata"] = {
                **(scaffold.get("metadata") or {}),
                "ai_generation_failed": str(exc),
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
            }
            return {
                "payload": scaffold,
                "trace": {
                    "generator": "ai",
                    "language": language,
                    "preferred_models": preferred_models,
                    "shard_index": shard_index,
                    "shard_key": shard_key,
                    "model": None,
                    "provider": None,
                    "token_usage": {},
                    "duration": 0.0,
                    "prompt": prompt,
                    "system_prompt": system,
                    "response": response,
                    "error": str(exc),
                    "cache_hit": False,
                    "retry_policy": self._retry_policy_metadata(),
                },
            }

        try:
            parsed = self._parse_json(response)
        except (json.JSONDecodeError, ValueError) as parse_exc:
            # A malformed answer must never remain reusable from the response
            # cache, and the responsible Model Center route receives a short
            # structured-output cooldown. The next repair therefore starts on
            # a different healthy route when one exists.
            await self._invalidate_failed_completions(agent, [(prompt, system)])
            await self._record_structured_output_failure(
                latest_conversation,
                parse_exc,
            )
            repair_exc: Exception = parse_exc
            while format_repair_budget > 0:
                format_repair_budget -= 1
                format_repair_attempted = True
                format_repair_prompt = self._json_format_repair_prompt(
                    previous_response=response or "",
                    language=language,
                    target_sections=target_sections,
                )
                repair_model = self._text_or_none(latest_conversation.get("model"))
                try:
                    response = await self._complete_with_policy(
                        agent,
                        prompt=format_repair_prompt,
                        system=(
                            "Repair JSON structure only. Preserve supported facts and citation markers. "
                            "Every non-target field must be null. Return JSON only."
                        ),
                        preferred_models=self._repair_preferred_models(
                            preferred_models,
                            repair_model,
                        ),
                    )
                    latest_conversation = agent.get_latest_conversation() or {}
                    parsed = self._parse_json(response)
                    break
                except Exception as exc:
                    repair_exc = exc
                    logger.warning(
                        "AI JSON format repair failed for {}/{}: {}",
                        disease.get("disease_id"),
                        language,
                        exc,
                    )
            else:
                scaffold["review_notes"] = f"AI JSON format repair failed: {repair_exc}"
                return {
                    "payload": scaffold,
                    "trace": {
                        "generator": "ai",
                        "language": language,
                        "preferred_models": preferred_models,
                        "shard_index": shard_index,
                        "shard_key": shard_key,
                        "model": self._text_or_none(latest_conversation.get("model")),
                        "provider": self._text_or_none(latest_conversation.get("provider")),
                        "token_usage": self._interaction_metrics(agent)["token_usage"],
                        "duration": time.time() - started_at,
                        "prompt": prompt,
                        "format_repair_prompt": format_repair_prompt,
                        "system_prompt": system,
                        "response": response,
                        "error": str(repair_exc),
                        "cache_hit": False,
                        "format_repair_attempted": format_repair_attempted,
                        "retry_policy": self._retry_policy_metadata(),
                    },
                }

        def assemble_payload(
            generated: dict[str, Any],
            conversation: dict[str, Any],
        ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
            usage = (
                conversation.get("tokens")
                if isinstance(conversation.get("tokens"), dict)
                else {}
            )
            used_model = self._text_or_none(conversation.get("model"))
            used_provider = self._text_or_none(conversation.get("provider"))
            conversation_metadata = (
                conversation.get("metadata")
                if isinstance(conversation.get("metadata"), dict)
                else {}
            )
            was_cache_hit = bool(conversation_metadata.get("cache_hit"))
            clinical_features = self._field(generated, "clinical_features")
            result = {
                **scaffold,
                "status": "published",
                "brief": self._field(generated, "brief"),
                "definition": self._field(generated, "definition"),
                "clinical_features": clinical_features,
                "epidemiology": self._field(generated, "epidemiology"),
                "transmission": self._field(generated, "transmission"),
                "prevention": self._field(generated, "prevention"),
                "surveillance_note": self._field(generated, "surveillance_note"),
                "clinical_summary": clinical_features,
                "risk_groups": self._field(generated, "risk_groups"),
                "source_ids": source_ids,
                "source_attribution": scaffold["source_attribution"],
                "disclaimer": DISCLAIMER_ZH if language == "zh" else DISCLAIMER_EN,
                "model": used_model or "ai-model-center",
                "metadata": {
                    **(scaffold.get("metadata") or {}),
                    "generator": "AIDiseaseBriefGenerator",
                    "ai_model": used_model,
                    "ai_provider": used_provider,
                    "preferred_models": preferred_models,
                    "shard_index": shard_index,
                    "shard_key": shard_key,
                    "token_usage": usage,
                    "cache_hit": was_cache_hit,
                    "version": 2,
                    "pipeline_version": 3,
                    "profile_schema": profile_schema.to_dict(),
                    "evidence_manifest": evidence_manifest.to_dict(),
                    "target_sections": target_sections,
                    "evidence_target_sections": evidence_target_sections,
                },
            }
            result = normalize_knowledge_citations(
                result,
                marker_mode="position",
                prune_uncited_sources=True,
            )
            result, result_assessment = apply_knowledge_quality_gate(result)
            return result, result_assessment, {
                "model": used_model,
                "provider": used_provider,
                "token_usage": usage,
                "cache_hit": was_cache_hit,
            }

        merged, assessment, trace_values = assemble_payload(parsed, latest_conversation)
        initial_citation_failures = validate_knowledge_citations(
            merged,
            fields=target_sections,
        )
        citation_repair_attempted = False
        final_citation_failures = list(initial_citation_failures)
        repair_prompt: str | None = None
        repair_error: str | None = None
        if initial_citation_failures and citation_repair_budget > 0:
            citation_repair_budget -= 1
            citation_repair_attempted = True
            repair_prompt = self._citation_repair_prompt(
                prompt_payload=prompt_payload,
                previous_response=response or "",
                failures=initial_citation_failures,
            )
            repair_preferred_models = self._repair_preferred_models(
                preferred_models,
                trace_values.get("model"),
            )
            try:
                repair_response = await self._complete_with_policy(
                    agent,
                    prompt=repair_prompt,
                    system=system,
                    preferred_models=repair_preferred_models,
                )
                repair_conversation = agent.get_latest_conversation() or {}
                repaired = self._parse_json(repair_response)
                merged, assessment, trace_values = assemble_payload(
                    repaired,
                    repair_conversation,
                )
                response = repair_response
                final_citation_failures = validate_knowledge_citations(
                    merged,
                    fields=target_sections,
                )
            except Exception as exc:
                repair_error = str(exc)
                logger.warning(
                    "AI citation repair failed for {}/{}: {}",
                    disease.get("disease_id"),
                    language,
                    exc,
                )

        post_repair_citation_failures = list(final_citation_failures)
        citation_fallback_fields = self._citation_failure_fields(
            final_citation_failures,
            target_sections=target_sections,
        )
        if citation_fallback_fields:
            for field in citation_fallback_fields:
                merged[field] = None
                if field == "clinical_features":
                    merged["clinical_summary"] = None
            merged = normalize_knowledge_citations(
                merged,
                marker_mode="position",
                prune_uncited_sources=True,
            )
            merged, assessment = apply_knowledge_quality_gate(merged)
            final_citation_failures = validate_knowledge_citations(
                merged,
                fields=target_sections,
            )

        quality_repair_attempted = False
        quality_repair_prompt: str | None = None
        quality_repair_error: str | None = None
        quality_repair_failures = self._repairable_quality_failures(
            assessment,
            target_sections=target_sections,
            evidence_manifest=evidence_manifest,
        )
        initial_quality_repair_failures = list(quality_repair_failures)
        quality_repair_attempt_count = 0
        while (
            quality_repair_failures
            and not final_citation_failures
            and quality_repair_budget > 0
        ):
            quality_repair_budget -= 1
            quality_repair_attempted = True
            quality_repair_attempt_count += 1
            quality_repair_prompt = self._quality_repair_prompt(
                prompt_payload=prompt_payload,
                failures=quality_repair_failures,
            )
            repair_preferred_models = self._repair_preferred_models(
                preferred_models,
                trace_values.get("model"),
            )
            try:
                quality_repair_response = await self._complete_with_policy(
                    agent,
                    prompt=quality_repair_prompt,
                    system=(
                        "Targeted knowledge-field repair mode. Return a JSON object containing "
                        "only the fields named in quality_failures. Each value must be substantive "
                        "source-grounded prose with valid citation_ref markers, or null when the "
                        "provided evidence cannot support it. Do not return unrelated keys."
                    ),
                    preferred_models=repair_preferred_models,
                )
                quality_repair_conversation = agent.get_latest_conversation() or {}
                repaired = self._parse_json(quality_repair_response)
                previous_merged = merged
                # The semantic-repair protocol is intentionally sparse: it
                # returns only failed fields. Build a fresh payload only to
                # collect the route trace, then patch those fields onto the
                # quality-checked profile. Re-assembling a sparse JSON object
                # would temporarily erase valid sections and makes the
                # preservation rule unnecessarily indirect.
                _, _, repair_trace_values = assemble_payload(
                    repaired,
                    quality_repair_conversation,
                )
                repaired_fields = {
                    str(item["field"])
                    for item in quality_repair_failures
                    if item.get("field")
                }
                merged = {
                    **previous_merged,
                    "metadata": {
                        **(previous_merged.get("metadata") or {}),
                        "ai_model": repair_trace_values.get("model"),
                        "ai_provider": repair_trace_values.get("provider"),
                        "token_usage": repair_trace_values.get("token_usage") or {},
                        "cache_hit": bool(repair_trace_values.get("cache_hit")),
                        "quality_repair_model": repair_trace_values.get("model"),
                        "quality_repair_provider": repair_trace_values.get("provider"),
                    },
                }
                if repair_trace_values.get("model"):
                    merged["model"] = repair_trace_values["model"]
                # The repair call is allowed to change only rejected target
                # fields. Keep already valid prose stable even if the model
                # returns a sparse or over-broad replacement payload.
                for field in (
                    "brief",
                    "definition",
                    "clinical_features",
                    "epidemiology",
                    "transmission",
                    "prevention",
                    "surveillance_note",
                    "risk_groups",
                ):
                    if field in repaired_fields:
                        merged[field] = self._field(repaired, field)
                merged["clinical_summary"] = merged.get("clinical_features")
                merged = normalize_knowledge_citations(
                    merged,
                    marker_mode="position",
                    prune_uncited_sources=True,
                )
                merged, assessment = apply_knowledge_quality_gate(merged)
                trace_values = repair_trace_values
                response = quality_repair_response
                final_citation_failures = validate_knowledge_citations(
                    merged,
                    fields=target_sections,
                )
                citation_fallback_fields = self._citation_failure_fields(
                    final_citation_failures,
                    target_sections=target_sections,
                )
                if citation_fallback_fields:
                    for field in citation_fallback_fields:
                        merged[field] = None
                        if field == "clinical_features":
                            merged["clinical_summary"] = None
                    merged = normalize_knowledge_citations(
                        merged,
                        marker_mode="position",
                        prune_uncited_sources=True,
                    )
                    merged, assessment = apply_knowledge_quality_gate(merged)
                    final_citation_failures = validate_knowledge_citations(
                        merged,
                        fields=target_sections,
                    )
                quality_repair_failures = self._repairable_quality_failures(
                    assessment,
                    target_sections=target_sections,
                    evidence_manifest=evidence_manifest,
                )
            except Exception as exc:
                quality_repair_error = str(exc)
                logger.warning(
                    "AI quality repair failed for {}/{}: {}",
                    disease.get("disease_id"),
                    language,
                    exc,
                )
                break

        # Citation fallback can deliberately remove unsupported optional
        # fields. If that leaves a schema-complete, grounded payload, the
        # earlier quality gate may still carry its pre-fallback ``draft``
        # status. Promote the repaired payload explicitly; otherwise a valid
        # result is retried forever as a misleading publication-status error.
        self._promote_publishable_payload(
            merged,
            assessment=assessment,
            citation_failures=final_citation_failures,
        )

        interaction_metrics = self._interaction_metrics(agent)
        if final_citation_failures or not assessment.publishable:
            await self._invalidate_failed_completions(
                agent,
                [
                    (prompt, system),
                    (
                        format_repair_prompt,
                        "Repair structure only. Preserve facts and citations. Return JSON only.",
                    ),
                    (repair_prompt, system),
                    (quality_repair_prompt, system),
                ],
            )
        merged["metadata"] = {
            **(merged.get("metadata") or {}),
            "prompt_protocol_version": self.PROMPT_PROTOCOL_VERSION,
            "token_usage": interaction_metrics["token_usage"],
            "cache_hit": bool(interaction_metrics["cache_hits"]),
            "ai_interaction": {
                **interaction_metrics,
                "system_characters": len(system),
                "prompt_characters": len(prompt),
                "estimated_input_tokens": self._estimate_tokens(system, prompt),
                "max_output_tokens": output_token_budget,
                "format_repair_attempted": format_repair_attempted,
                "format_repair_prompt_characters": len(format_repair_prompt or ""),
                "citation_repair_prompt_characters": len(repair_prompt or ""),
                "quality_repair_prompt_characters": len(quality_repair_prompt or ""),
                "quality_repair_attempt_count": quality_repair_attempt_count,
                "retry_policy": self._retry_policy_metadata(),
            },
            "citation_repair": {
                "attempted": citation_repair_attempted,
                "initial_failures": initial_citation_failures,
                "post_repair_failures": post_repair_citation_failures,
                "fallback_fields": citation_fallback_fields,
                "final_failures": final_citation_failures,
                "error": repair_error,
            },
            "quality_repair": {
                "attempted": quality_repair_attempted,
                "failures": initial_quality_repair_failures,
                "error": quality_repair_error,
            },
        }
        if assessment.publishable:
            merged["review_notes"] = (
                "AI-generated, source-grounded brief; ready for human spot review."
                if assessment.display_mode == "full"
                else "AI-generated partial brief; unsupported fields were omitted and remain queued for enrichment."
            )
        return {
            "payload": merged,
            "trace": {
                "generator": "ai",
                "language": language,
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
                "model": trace_values.get("model"),
                "provider": trace_values.get("provider"),
                "token_usage": interaction_metrics["token_usage"],
                "duration": time.time() - started_at,
                "prompt": prompt,
                "format_repair_prompt": format_repair_prompt,
                "repair_prompt": repair_prompt,
                "system_prompt": system,
                "response": response,
                "error": repair_error,
                "cache_hit": bool(interaction_metrics["cache_hits"]),
                "format_repair_attempted": format_repair_attempted,
                "citation_repair_attempted": citation_repair_attempted,
                "quality_repair_attempt_count": quality_repair_attempt_count,
                "citation_failures": final_citation_failures,
                "interaction_metrics": interaction_metrics,
                "retry_policy": self._retry_policy_metadata(),
            },
        }

    async def translate_from_payload_with_trace(
        self,
        *,
        disease: dict[str, Any],
        english_payload: dict[str, Any],
        sources: list[dict[str, Any]],
        target_sections: list[str],
        language: str = "zh",
    ) -> dict[str, Any]:
        """Translate a source-grounded English payload without replaying evidence text."""
        language = "zh" if language == "zh" else language
        if language != "zh":
            raise ValueError("Knowledge payload translation currently supports zh only")

        profile_schema = resolve_knowledge_profile_schema(disease)
        public_sources = (
            list(sources)
            if disease.get("_evidence_packet_prepared")
            else self._usable_public_sources(sources, disease=disease)
        )
        scaffold = self._empty_scaffold(
            disease=disease,
            sources=public_sources,
            language=language,
            profile_schema=profile_schema,
            target_sections=target_sections,
        )
        preferred_models, shard_index, shard_key = await self._preferred_models_for(
            disease_id=str(disease.get("disease_id") or ""),
            language=language,
        )
        source_ids = [src.get("id") for src in public_sources if src.get("id") is not None]
        source_attribution = (
            english_payload.get("source_attribution")
            if isinstance(english_payload.get("source_attribution"), list)
            else scaffold["source_attribution"]
        )
        english_metadata = (
            english_payload.get("metadata")
            if isinstance(english_payload.get("metadata"), dict)
            else {}
        )
        evidence_manifest = (
            english_metadata.get("evidence_manifest")
            if isinstance(english_metadata.get("evidence_manifest"), dict)
            else build_evidence_manifest(
                public_sources,
                profile_schema,
                target_sections=disease.get("evidence_target_sections") or target_sections,
                entity_aliases=disease.get("evidence_entity_aliases") or (),
                max_total_characters=int(get_config().ai.knowledge_evidence_manifest_max_characters),
            ).to_dict()
        )
        translation_payload = {
            "protocol_version": self.PROMPT_PROTOCOL_VERSION,
            "output_language": "zh",
            "target_sections": target_sections,
            "source_policy": (
                "Translate only the supplied English JSON. Preserve every citation marker exactly "
                "where it supports the translated claim. Do not add, infer, remove, merge facts, "
                "summarize, or shorten sections."
            ),
            "disease": {
                "disease_id": disease.get("disease_id"),
                "name_en": disease.get("name_en") or disease.get("standard_name_en"),
                "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
            },
            "profile_schema": {
                "profile_type": profile_schema.profile_type,
                "required_fields": list(profile_schema.required_fields),
                "optional_fields": list(profile_schema.optional_fields),
                "not_applicable_fields": list(profile_schema.not_applicable_fields),
            },
            "english_json": {
                field: english_payload.get(field)
                for field in (
                    "brief",
                    "definition",
                    "clinical_features",
                    "epidemiology",
                    "transmission",
                    "prevention",
                    "surveillance_note",
                    "risk_groups",
                )
                if field in target_sections
            },
        }
        system = (
            f"Knowledge-profile protocol {self.PROMPT_PROTOCOL_VERSION}; translation mode. "
            "Return JSON only with exactly these keys: brief, definition, clinical_features, "
            "epidemiology, transmission, prevention, surveillance_note, risk_groups. Translate "
            "English public-health prose into fluent Chinese. Preserve citation markers exactly; "
            "null stays null. Preserve the source section's level of detail and sentence count "
            "as closely as Chinese readability allows. Never add facts or citations."
        )
        prompt = (
            "Translate the target_sections in english_json into Chinese. For keys absent from "
            "english_json, return null. JSON only.\n"
            f"{json.dumps(translation_payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        output_token_budget = self._output_token_budget(target_sections)
        agent = self._spawn_agent(max_tokens=output_token_budget)
        started_at = time.time()
        response: str | None = None
        latest_conversation: dict[str, Any] = {}
        try:
            response = await self._complete_with_policy(
                agent,
                prompt=prompt,
                system=system,
                preferred_models=preferred_models,
            )
            latest_conversation = agent.get_latest_conversation() or {}
            parsed = self._parse_json(response)
        except Exception as exc:
            logger.warning(
                "AI disease brief translation failed for {}/{}: {}",
                disease.get("disease_id"),
                language,
                exc,
            )
            scaffold["review_notes"] = f"AI translation failed: {exc}"
            scaffold["metadata"] = {
                **(scaffold.get("metadata") or {}),
                "ai_translation_failed": str(exc),
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
            }
            return {
                "payload": scaffold,
                "trace": {
                    "generator": "ai_translation",
                    "language": language,
                    "preferred_models": preferred_models,
                    "shard_index": shard_index,
                    "shard_key": shard_key,
                    "model": self._text_or_none(latest_conversation.get("model")),
                    "provider": self._text_or_none(latest_conversation.get("provider")),
                    "token_usage": self._interaction_metrics(agent)["token_usage"],
                    "duration": time.time() - started_at,
                    "prompt": prompt,
                    "system_prompt": system,
                    "response": response,
                    "error": str(exc),
                    "cache_hit": False,
                    "retry_policy": self._retry_policy_metadata(),
                },
            }

        usage = latest_conversation.get("tokens") if isinstance(latest_conversation.get("tokens"), dict) else {}
        used_model = self._text_or_none(latest_conversation.get("model"))
        used_provider = self._text_or_none(latest_conversation.get("provider"))
        conversation_metadata = (
            latest_conversation.get("metadata")
            if isinstance(latest_conversation.get("metadata"), dict)
            else {}
        )
        clinical_features = self._field(parsed, "clinical_features")
        merged = {
            **scaffold,
            "brief": self._field(parsed, "brief"),
            "definition": self._field(parsed, "definition"),
            "clinical_features": clinical_features,
            "epidemiology": self._field(parsed, "epidemiology"),
            "transmission": self._field(parsed, "transmission"),
            "prevention": self._field(parsed, "prevention"),
            "surveillance_note": self._field(parsed, "surveillance_note"),
            "clinical_summary": clinical_features,
            "risk_groups": self._field(parsed, "risk_groups"),
            "source_ids": source_ids,
            "source_attribution": source_attribution,
            "disclaimer": DISCLAIMER_ZH,
            "model": used_model or "ai-model-center",
            "status": "published",
            "metadata": {
                **(scaffold.get("metadata") or {}),
                "generator": "AIDiseaseBriefGenerator",
                "translation_mode": "from_en_grounded_payload",
                "translation_source_language": "en",
                "ai_model": used_model,
                "ai_provider": used_provider,
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
                "token_usage": usage,
                "cache_hit": bool(conversation_metadata.get("cache_hit")),
                "version": 2,
                "pipeline_version": 3,
                "profile_schema": profile_schema.to_dict(),
                "evidence_manifest": evidence_manifest,
                "target_sections": target_sections,
                "evidence_target_sections": list(
                    disease.get("evidence_target_sections") or target_sections
                ),
            },
        }
        merged = normalize_knowledge_citations(
            merged,
            marker_mode="auto",
            prune_uncited_sources=True,
        )
        merged, assessment = apply_knowledge_quality_gate(merged)
        citation_failures = validate_knowledge_citations(merged, fields=target_sections)
        if citation_failures:
            await self._invalidate_failed_completions(agent, [(prompt, system)])
        interaction_metrics = self._interaction_metrics(agent)
        merged["metadata"] = {
            **(merged.get("metadata") or {}),
            "prompt_protocol_version": self.PROMPT_PROTOCOL_VERSION,
            "token_usage": interaction_metrics["token_usage"],
            "cache_hit": bool(interaction_metrics["cache_hits"]),
            "ai_interaction": {
                **interaction_metrics,
                "system_characters": len(system),
                "prompt_characters": len(prompt),
                "estimated_input_tokens": self._estimate_tokens(system, prompt),
                "max_output_tokens": output_token_budget,
                "retry_policy": self._retry_policy_metadata(),
            },
            "citation_repair": {
                "attempted": False,
                "initial_failures": citation_failures,
                "post_repair_failures": citation_failures,
                "fallback_fields": [],
                "final_failures": citation_failures,
                "error": None,
            },
        }
        if citation_failures:
            merged["status"] = "draft"
            merged["metadata"] = {
                **(merged.get("metadata") or {}),
                "automation_state": "awaiting_evidence",
                "block_reason": "citation_validation_failed",
            }
        return {
            "payload": merged,
            "trace": {
                "generator": "ai_translation",
                "language": language,
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
                "model": used_model,
                "provider": used_provider,
                "token_usage": interaction_metrics["token_usage"],
                "duration": time.time() - started_at,
                "prompt": prompt,
                "system_prompt": system,
                "response": response,
                "error": None,
                "cache_hit": bool(interaction_metrics["cache_hits"]),
                "citation_failures": citation_failures,
                "interaction_metrics": interaction_metrics,
                "retry_policy": self._retry_policy_metadata(),
            },
        }

    @classmethod
    def _usable_public_sources(
        cls,
        sources: list[dict[str, Any]],
        *,
        disease: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        disease = disease or {}
        schema = resolve_knowledge_profile_schema(disease)
        targets = (
            list(disease.get("evidence_target_sections") or disease.get("target_sections") or [])
            or list(schema.required_fields)
        )
        packet = prepare_evidence_packet(
            sources,
            schema,
            target_sections=targets,
            entity_aliases=disease.get("evidence_entity_aliases") or (),
            max_sources=int(get_config().ai.knowledge_evidence_max_sources),
            max_manifest_characters=int(get_config().ai.knowledge_evidence_manifest_max_characters),
            allowed_source_types=cls.PUBLIC_SOURCE_TYPES,
        )
        return list(packet.sources)

    @classmethod
    def _empty_scaffold(
        cls,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
        profile_schema: Any,
        target_sections: list[str],
    ) -> dict[str, Any]:
        source_types = {str(source.get("source_type") or "") for source in sources}
        confidence = (
            "high"
            if source_types & cls.AUTHORITATIVE_SOURCE_TYPES
            else "medium"
            if source_types & {"wikidata", "wikipedia", "pubmed"}
            else "low"
        )
        attribution = []
        for source in sources:
            metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
            attribution.append(
                {
                    "id": source.get("id"),
                    "source_id": source.get("source_id") or source.get("id"),
                    "source_name": source.get("source_name"),
                    "source_type": source.get("source_type"),
                    "title": source.get("title"),
                    "url": source.get("url"),
                    "resolved_url": source.get("resolved_url") or source.get("url"),
                    "license": source.get("license"),
                    "fetched_at": source.get("fetched_at"),
                    "pmid": metadata.get("pmid") or source.get("pmid"),
                    "doi": metadata.get("doi") or source.get("doi"),
                    "first_author": metadata.get("first_author") or source.get("first_author"),
                    "journal": metadata.get("journal") or source.get("journal"),
                    "pub_date": metadata.get("pub_date") or source.get("pub_date"),
                    "container_title": metadata.get("container_title") or source.get("container_title"),
                    "publisher": metadata.get("publisher") or source.get("publisher"),
                    "year": metadata.get("year") or source.get("year"),
                    "provider": metadata.get("provider") or source.get("provider"),
                    "content_kind": metadata.get("content_kind") or source.get("content_kind"),
                    "metadata": metadata,
                }
            )
        return {
            "disease_id": disease.get("disease_id"),
            "language": language,
            "brief": None,
            "definition": None,
            "clinical_features": None,
            "epidemiology": None,
            "clinical_summary": None,
            "transmission": None,
            "prevention": None,
            "surveillance_note": None,
            "risk_groups": None,
            "source_ids": [source.get("id") for source in sources if source.get("id") is not None],
            "source_attribution": attribution,
            "disclaimer": DISCLAIMER_ZH if language == "zh" else DISCLAIMER_EN,
            "model": "ai-model-center",
            "status": "draft",
            "source_confidence": confidence,
            "quality_score": 0.0,
            "review_notes": "Awaiting evidence-grounded AI generation.",
            "metadata": {
                "source_types": sorted(source_types),
                "generator": "AIDiseaseBriefGenerator",
                "version": 1,
                "profile_schema": profile_schema.to_dict(),
                "target_sections": target_sections,
                "automation_state": "awaiting_evidence",
            },
        }

    def _spawn_agent(self, *, max_tokens: int) -> KnowledgeBriefAgent:
        if self.agent is not None:
            self.agent.clear_conversation_history()
            if hasattr(self.agent, "max_tokens"):
                self.agent.max_tokens = max_tokens
            return self.agent
        return KnowledgeBriefAgent(max_tokens=max_tokens)

    async def _complete_with_policy(
        self,
        agent: Any,
        *,
        prompt: str,
        system: str,
        preferred_models: list[str],
    ) -> str:
        config = get_config().ai
        output_token_budget = max(
            1,
            int(getattr(agent, "max_tokens", config.knowledge_max_output_tokens)),
        )
        return await asyncio.wait_for(
            agent.complete(
                prompt=prompt,
                system=system,
                use_cache=True,
                preferred_models=preferred_models,
                max_quota_recovery_rounds=config.knowledge_quota_recovery_rounds,
                wait_for_model_recovery=config.knowledge_wait_for_model_recovery,
                model_request_timeout_seconds=self._effective_model_request_timeout_seconds(
                    output_token_budget=output_token_budget,
                ),
                max_attempts_per_model=config.knowledge_model_attempts_per_route,
                timeout_cooldown_seconds=config.knowledge_timeout_cooldown_seconds,
                response_format={"type": "json_object"},
            ),
            timeout=float(config.knowledge_generation_timeout_seconds),
        )

    @staticmethod
    async def _record_structured_output_failure(
        conversation: dict[str, Any],
        error: BaseException,
    ) -> None:
        metadata = conversation.get("metadata") if isinstance(conversation, dict) else None
        route = metadata.get("runtime_route") if isinstance(metadata, dict) else None
        if not isinstance(route, dict) or not route.get("model_id") or not route.get("provider_id"):
            return
        try:
            await record_route_runtime_failure(
                route,
                RuntimeError(f"Malformed structured response: {error}"),
                cooldown_seconds=max(5, int(get_config().ai.knowledge_timeout_cooldown_seconds)),
            )
        except Exception as exc:
            logger.warning("Failed to record malformed structured output for Model Center: {}", exc)

    @staticmethod
    def _interaction_metrics(agent: Any) -> dict[str, Any]:
        if hasattr(agent, "get_conversation_history"):
            history = agent.get_conversation_history() or []
        elif hasattr(agent, "get_latest_conversation"):
            latest = agent.get_latest_conversation()
            history = [latest] if latest else []
        else:
            history = []
        token_usage = {"prompt": 0, "completion": 0, "total": 0}
        cache_hits = 0
        network_completions = 0
        for conversation in history:
            if not isinstance(conversation, dict):
                continue
            metadata = conversation.get("metadata")
            is_cache_hit = bool(
                isinstance(metadata, dict) and metadata.get("cache_hit")
            )
            if is_cache_hit:
                cache_hits += 1
                continue
            network_completions += 1
            usage = conversation.get("tokens")
            if not isinstance(usage, dict):
                continue
            for key in token_usage:
                try:
                    token_usage[key] += int(usage.get(key) or 0)
                except (TypeError, ValueError):
                    continue
        if token_usage["total"] == 0:
            token_usage["total"] = token_usage["prompt"] + token_usage["completion"]
        return {
            "successful_completions": len(history),
            "network_completions": network_completions,
            "cache_hits": cache_hits,
            "token_usage": token_usage,
        }

    @staticmethod
    async def _invalidate_failed_completions(
        agent: Any,
        attempts: list[tuple[str | None, str | None]],
    ) -> None:
        invalidate = getattr(agent, "invalidate_completion_cache", None)
        if not callable(invalidate):
            return
        for prompt, system in attempts:
            if not prompt:
                continue
            try:
                await invalidate(prompt=prompt, system=system)
            except Exception as exc:
                logger.warning("Failed to evict rejected knowledge completion: {}", exc)

    @staticmethod
    def _promote_publishable_payload(
        payload: dict[str, Any],
        *,
        assessment: Any,
        citation_failures: list[str],
    ) -> None:
        """Restore publication after a bounded repair made the payload valid."""

        if citation_failures or not bool(getattr(assessment, "publishable", False)):
            return
        payload["status"] = "published"
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return
        if metadata.get("automation_state") == "awaiting_evidence":
            metadata.pop("automation_state", None)
        if metadata.get("block_reason") in {
            "missing_required_sections",
            "citation_validation_failed",
        }:
            metadata.pop("block_reason", None)
        metadata["publication_status_recovered"] = "post_repair_quality_gate"

    @staticmethod
    def _citation_failure_fields(
        failures: list[str],
        *,
        target_sections: list[str],
    ) -> list[str]:
        allowed = set(target_sections)
        fields: list[str] = []
        for failure in failures:
            field, separator, _detail = str(failure).partition(":")
            field = field.strip()
            if separator and field in allowed and field not in fields:
                fields.append(field)
        return fields

    @staticmethod
    def _estimate_tokens(*parts: str) -> int:
        return (sum(len(part or "") for part in parts) + 3) // 4

    @staticmethod
    def _output_token_budget(target_sections: list[str]) -> int:
        maximum = int(get_config().ai.knowledge_max_output_tokens)
        requested = max(1, len(set(target_sections)))
        return min(maximum, max(800, 500 + (requested * 400)))

    @staticmethod
    def _retry_policy_metadata() -> dict[str, Any]:
        config = get_config().ai
        return {
            "attempts_per_route": config.knowledge_model_attempts_per_route,
            "configured_route_timeout_seconds": config.knowledge_model_request_timeout_seconds,
            "route_timeout_seconds": AIDiseaseBriefGenerator._effective_model_request_timeout_seconds(),
            "route_timeout_cap_seconds": config.knowledge_model_request_timeout_cap_seconds,
            "timeout_cooldown_seconds": config.knowledge_timeout_cooldown_seconds,
            "output_repair_attempts": config.knowledge_output_repair_attempts,
            "wait_for_model_recovery": config.knowledge_wait_for_model_recovery,
            "quota_recovery_rounds": config.knowledge_quota_recovery_rounds,
        }

    @staticmethod
    def _effective_model_request_timeout_seconds(
        *,
        output_token_budget: int | None = None,
    ) -> int:
        """Bound route patience to the actual structured-output workload.

        A complete bilingual profile legitimately needs more time than a
        one-field retry.  Giving both the same 120-second route timeout turns
        a missing `surveillance_note` into a costly full-workload failure.
        The budget is linearly interpolated between a measured viable floor
        for a compact structured response and the configured full-profile cap.
        """
        config = get_config().ai
        maximum = min(
            int(config.knowledge_model_request_timeout_seconds),
            int(config.knowledge_model_request_timeout_cap_seconds),
        )
        if output_token_budget is None:
            return maximum
        full_profile_budget = max(800, int(config.knowledge_max_output_tokens))
        compact_budget = min(800, full_profile_budget)
        compact_timeout = min(maximum, 55)
        normalized_budget = max(compact_budget, min(int(output_token_budget), full_profile_budget))
        if full_profile_budget == compact_budget:
            return maximum
        fraction = (normalized_budget - compact_budget) / (full_profile_budget - compact_budget)
        return max(
            compact_timeout,
            min(maximum, round(compact_timeout + ((maximum - compact_timeout) * fraction))),
        )

    @staticmethod
    def _text_or_none(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    @staticmethod
    async def _preferred_models_for(*, disease_id: str, language: str) -> tuple[list[str], int, str]:
        try:
            routes = await get_runtime_routes()
        except Exception as exc:
            logger.warning("Failed to load model-center routes for knowledge shard selection: {}", exc)
            routes = []

        reliable_models: list[str] = []
        fallback_models: list[str] = []
        for route in routes:
            if not route.get("has_api_key"):
                continue
            if str(route.get("last_check_status") or "").strip().lower() == "unavailable":
                continue
            if not route.get("available_for_routing", True):
                continue
            model_name = str(route.get("model_name") or "").strip()
            if not model_name or model_name in reliable_models or model_name in fallback_models:
                continue
            if AIDiseaseBriefGenerator._is_reliable_knowledge_route(route):
                reliable_models.append(model_name)
            else:
                fallback_models.append(model_name)

        shard_key = f"{(disease_id or '').strip().upper()}:{language}"
        shard_models = reliable_models or fallback_models
        if not shard_models:
            return [], 0, shard_key

        digest = hashlib.md5(shard_key.encode("utf-8")).hexdigest()
        shard_index = int(digest[:8], 16) % len(shard_models)
        ordered = shard_models[shard_index:] + shard_models[:shard_index]
        if reliable_models:
            return [*ordered, *fallback_models], shard_index, shard_key
        return ordered, shard_index, shard_key

    @staticmethod
    def _is_reliable_knowledge_route(route: dict[str, Any]) -> bool:
        """Keep sticky knowledge shards off routes with repeated live timeouts.

        A one-off transport fault remains a fallback candidate. Repeated real
        production failures, however, should not receive new primary work just
        because a cooldown expired. The complete route list remains available
        to BaseAgent as a final fallback, so this does not turn health scoring
        into a hard model exclusion.
        """

        def metric(name: str) -> int:
            try:
                return max(0, int(route.get(name) or 0))
            except (TypeError, ValueError):
                return 0

        failure_streak = metric("runtime_failure_streak")
        failures = metric("runtime_failure_count")
        timeouts = metric("runtime_timeout_count")
        successes = metric("runtime_success_count")
        total_outcomes = successes + failures
        failure_ratio = failures / total_outcomes if total_outcomes else 0.0

        if failure_streak > 0:
            return False
        # A route with a small history remains eligible while it warms up. Once
        # enough live calls exist, a timeout-heavy route is retained only as a
        # fallback until it demonstrates a successful recovery.
        return not (
            failures >= 6
            and timeouts >= 3
            and failure_ratio >= 0.35
        )

    @staticmethod
    def _repair_preferred_models(
        preferred_models: list[str],
        failed_model: str | None,
    ) -> list[str]:
        """Try a different live route before retrying a malformed response."""
        if not failed_model:
            return list(preferred_models)
        alternatives = [model for model in preferred_models if model != failed_model]
        return [*alternatives, failed_model]

    @staticmethod
    def _system_prompt(_language: str) -> str:
        return (
            f"Knowledge-profile protocol {AIDiseaseBriefGenerator.PROMPT_PROTOCOL_VERSION}; "
            "schema version 4. Produce a conservative public-health profile using only the supplied "
            "evidence_manifest and catalogue context—never outside knowledge. A fragment may support "
            "only its supported_sections; inherited fragments remain limited to their allowed scope. "
            "If evidence does not support a field, return null for that field and never write an absence "
            "explanation or placeholder. Respect profile applicability: classification entities need "
            "scope boundaries; occupational, injury, poisoning, and violence entities need exposure "
            "mechanisms rather than invented infection language. Do not provide diagnosis, treatment, "
            "dosing, or personal advice. Paraphrase rather than copying source passages.\n"
            "Epidemiology must describe the target disease/entity itself: occurrence, burden, "
            "distribution, outbreaks, affected settings, or surveillance-relevant distribution. Do not "
            "use complications, opportunistic infections, co-infections, or comorbid conditions as the "
            "main epidemiology unless the target entity is explicitly that condition. If evidence is "
            "indirect, state only directly supported disease-level facts and leave unsupported details "
            "null.\n"
            "surveillance_note is required when the evidence_manifest contains surveillance, reporting, "
            "outbreak, monitoring, case definition, public-health response, source-data interpretation, "
            "or country/source-series interpretation signals. If supported, write 2-4 concise sentences "
            "explaining how this disease/entity should be interpreted in surveillance or public-health "
            "data. Return null only when no such evidence is present.\n"
            "Citations are mandatory in every non-null field. Use only sequential citation_ref markers "
            "such as [1] or [1][2], immediately after the supported claim. Never cite database IDs.\n"
            "Write fluent scholarly prose in the payload's output_language. Return JSON only with "
            "exactly these keys: "
            "brief, definition, clinical_features, epidemiology, transmission, prevention, "
            "surveillance_note, risk_groups. Values are substantive strings or null. Prefer concise "
            "2-4 sentence sections; a brief may contain 2-5 sentences. For prevention, risk_groups, "
            "and surveillance_note, avoid one-sentence minimal answers when evidence supports more "
            "detail. Every key outside target_sections must be null; do not expand a narrow repair "
            "into a full profile."
        )

    @classmethod
    def _prompt_payload(
        cls,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
        evidence_manifest: EvidenceManifest | None = None,
    ) -> dict[str, Any]:
        profile_schema = resolve_knowledge_profile_schema(disease)
        target_sections = (
            list(disease.get("target_sections") or [])
            if "target_sections" in disease
            else list(profile_schema.required_fields)
        )
        evidence_target_sections = list(
            disease.get("evidence_target_sections") or target_sections or profile_schema.required_fields
        )
        evidence_manifest = evidence_manifest or build_evidence_manifest(
            sources[: int(get_config().ai.knowledge_evidence_max_sources)],
            profile_schema,
            target_sections=evidence_target_sections,
            entity_aliases=disease.get("evidence_entity_aliases") or (),
            max_total_characters=int(get_config().ai.knowledge_evidence_manifest_max_characters),
        )
        source_payload = []
        for index, src in enumerate(sources[: int(get_config().ai.knowledge_evidence_max_sources)], start=1):
            source_metadata = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
            source_payload.append(
                {
                    "citation_ref": index,
                    "source_type": src.get("source_type"),
                    "source_name": src.get("source_name"),
                    "title": src.get("title"),
                    "url": src.get("url"),
                    "inherited_from_disease_id": source_metadata.get("inherited_from_disease_id"),
                    "allowed_sections": source_metadata.get("allowed_sections"),
                }
            )
        ontology = disease.get("ontology_context")
        related = ontology.get("related_entities") if isinstance(ontology, dict) else []
        return {
            "protocol_version": cls.PROMPT_PROTOCOL_VERSION,
            "disease": {
                "disease_id": disease.get("disease_id"),
                "name_en": disease.get("name_en") or disease.get("standard_name_en"),
                "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
                "category": disease.get("category"),
                "description": disease.get("description"),
                "icd_10": disease.get("icd_10"),
                "icd_11": disease.get("icd_11"),
                "related_entities": related or [],
            },
            "profile_schema": {
                "profile_type": profile_schema.profile_type,
                "required_fields": list(profile_schema.required_fields),
                "optional_fields": list(profile_schema.optional_fields),
                "not_applicable_fields": list(profile_schema.not_applicable_fields),
            },
            "target_sections": target_sections,
            "repair_context": list(disease.get("repair_context") or [])[:3],
            "evidence_manifest": evidence_manifest.to_dict(
                include_text=True,
                include_source_ids=False,
                include_content_hashes=False,
            ),
            "sources": source_payload,
            "evidence_budget": {
                "max_sources": int(get_config().ai.knowledge_evidence_max_sources),
                "max_manifest_characters": int(get_config().ai.knowledge_evidence_manifest_max_characters),
            },
            # Keep the sole bilingual difference at the end so providers with
            # prefix caching can reuse the long evidence prefix.
            "output_language": language,
        }

    @classmethod
    def _user_prompt(
        cls,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
        evidence_manifest: EvidenceManifest | None = None,
    ) -> str:
        payload = cls._prompt_payload(
            disease=disease,
            sources=sources,
            language=language,
            evidence_manifest=evidence_manifest,
        )
        return (
            "Generate only target_sections; every other key must be null. Use evidence_manifest as the sole factual boundary; "
            "sources contains attribution labels only. If no allowed fragment supports a field, set it "
            "to null—never guess or add an absence explanation. When repair_context is present, treat it as "
            "deterministic validation feedback: correct exactly the named target fields and citations, without "
            "inventing new facts. JSON only.\n"
            f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )

    @staticmethod
    def _citation_repair_prompt(
        *,
        prompt_payload: dict[str, Any],
        previous_response: str,
        failures: list[str],
    ) -> str:
        repair_payload = {
            "output_language": prompt_payload.get("output_language"),
            "target_sections": prompt_payload.get("target_sections"),
            "evidence_manifest": prompt_payload.get("evidence_manifest"),
            "failures": failures,
            "previous_json": previous_response,
        }
        return (
            "Repair the previous JSON using this validation payload. Rewrite an invalid field only "
            "from fragments that explicitly support it, otherwise set it to null. Do not attach a new "
            "citation to an unchanged unsupported claim. Return all eight keys as JSON only.\n"
            f"{json.dumps(repair_payload, ensure_ascii=False, separators=(',', ':'))}"
        )

    @staticmethod
    def _repairable_quality_failures(
        assessment: Any,
        *,
        target_sections: list[str],
        evidence_manifest: EvidenceManifest,
    ) -> list[dict[str, str]]:
        """Return target-field quality gaps that the supplied evidence can repair."""
        supported = {
            field
            for fragment in evidence_manifest.fragments
            for field in fragment.supported_sections
        }
        failures: list[dict[str, str]] = []
        for field in target_sections:
            field_assessment = assessment.fields.get(field)
            if field_assessment is None or field_assessment.available:
                continue
            if field not in supported:
                continue
            failures.append(
                {
                    "field": field,
                    "status": field_assessment.status,
                    "reason": field_assessment.reason or "quality_gate_rejected",
                }
            )
        return failures

    @staticmethod
    def _quality_repair_prompt(
        *,
        prompt_payload: dict[str, Any],
        failures: list[dict[str, str]],
    ) -> str:
        failure_fields = {
            str(item.get("field") or "").strip()
            for item in failures
            if str(item.get("field") or "").strip()
        }
        manifest = prompt_payload.get("evidence_manifest")
        fragments = manifest.get("fragments") if isinstance(manifest, dict) else []
        targeted_fragments = [
            fragment
            for fragment in fragments
            if isinstance(fragment, dict)
            and failure_fields.intersection(
                str(section) for section in fragment.get("supported_sections") or []
            )
        ]
        repair_payload = {
            "output_language": prompt_payload.get("output_language"),
            "quality_failures": failures,
            "evidence_manifest": {
                "target_sections": sorted(failure_fields),
                "fragments": targeted_fragments,
            },
        }
        return (
            "Repair only the fields named in quality_failures. Each named field has explicit "
            "support in this minimal evidence_manifest and must become substantive cited prose in "
            "the output language. Use only supporting fragments, and set a field to null if it "
            "cannot be supported exactly. Return a JSON object containing only named failure fields.\n"
            f"{json.dumps(repair_payload, ensure_ascii=False, separators=(',', ':'))}"
        )

    @staticmethod
    def _json_format_repair_prompt(
        *,
        previous_response: str,
        language: str,
        target_sections: list[str],
    ) -> str:
        return (
            "Convert the supplied model response into one valid JSON object with exactly these keys: "
            "brief, definition, clinical_features, epidemiology, transmission, prevention, "
            "surveillance_note, risk_groups. Preserve wording and citation markers for target_sections "
            "only; do not add facts. All non-target keys must be null. Use null for missing values and "
            "output JSON only. "
            f"Language: {language}. Target sections: {json.dumps(target_sections)}. Response: {previous_response}"
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Some otherwise-valid providers append a short natural-language
            # acknowledgement after the requested object.  A greedy regex
            # joins that acknowledgement (or a second object) into invalid
            # JSON.  Decode one complete object instead, accepting only the
            # first structurally valid object in the response.
            decoder = json.JSONDecoder()
            parsed = None
            for match in re.finditer(r"\{", text):
                try:
                    candidate, _ = decoder.raw_decode(text, match.start())
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
            if parsed is None:
                raise
        if not isinstance(parsed, dict):
            raise ValueError("AI response was not a JSON object")
        return parsed

    @staticmethod
    def _field(payload: dict[str, Any], key: str) -> str | None:
        if key in payload and payload.get(key) is None:
            return None
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        return " ".join(value.split())
