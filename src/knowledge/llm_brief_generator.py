"""LLM-backed disease brief generation using the AI model center."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

from src.ai.agents.base import BaseAgent
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

    PROMPT_PROTOCOL_VERSION = 3
    PUBLIC_SOURCE_TYPES = {"who", "who_don", "web_search", "wikidata", "wikipedia", "pubmed"}
    AUTHORITATIVE_SOURCE_TYPES = {"who", "who_don"}

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
        preferred_models, shard_index, shard_key = self._preferred_models_for(
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
        output_repair_budget = int(get_config().ai.knowledge_output_repair_attempts)

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
            if output_repair_budget <= 0:
                logger.warning(
                    "AI JSON parsing failed for {}/{} and output repair is disabled: {}",
                    disease.get("disease_id"),
                    language,
                    parse_exc,
                )
                scaffold["review_notes"] = f"AI JSON parsing failed: {parse_exc}"
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
                        "token_usage": {},
                        "duration": time.time() - started_at,
                        "prompt": prompt,
                        "system_prompt": system,
                        "response": response,
                        "error": str(parse_exc),
                        "cache_hit": False,
                        "format_repair_attempted": False,
                        "retry_policy": self._retry_policy_metadata(),
                    },
                }
            output_repair_budget -= 1
            format_repair_attempted = True
            format_repair_prompt = self._json_format_repair_prompt(
                previous_response=response,
                language=language,
            )
            repair_model = self._text_or_none(latest_conversation.get("model"))
            try:
                response = await self._complete_with_policy(
                    agent,
                    prompt=format_repair_prompt,
                    system="Repair structure only. Preserve facts and citations. Return JSON only.",
                    preferred_models=[repair_model] if repair_model else preferred_models,
                )
                latest_conversation = agent.get_latest_conversation() or {}
                parsed = self._parse_json(response)
            except Exception as repair_exc:
                logger.warning(
                    "AI JSON format repair failed for {}/{}: {}",
                    disease.get("disease_id"),
                    language,
                    repair_exc,
                )
                scaffold["review_notes"] = f"AI JSON format repair failed: {repair_exc}"
                return {
                    "payload": scaffold,
                    "trace": {
                        "generator": "ai",
                        "language": language,
                        "preferred_models": preferred_models,
                        "shard_index": shard_index,
                        "shard_key": shard_key,
                        "model": repair_model,
                        "provider": self._text_or_none(latest_conversation.get("provider")),
                        "token_usage": self._interaction_metrics(agent)["token_usage"],
                        "duration": time.time() - started_at,
                        "prompt": prompt,
                        "format_repair_prompt": format_repair_prompt,
                        "system_prompt": system,
                        "response": response,
                        "error": str(repair_exc),
                        "cache_hit": False,
                        "format_repair_attempted": True,
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
                    "pipeline_version": 2,
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
        if initial_citation_failures and output_repair_budget > 0:
            output_repair_budget -= 1
            citation_repair_attempted = True
            repair_prompt = self._citation_repair_prompt(
                prompt_payload=prompt_payload,
                previous_response=response or "",
                failures=initial_citation_failures,
            )
            repair_preferred_models = (
                [trace_values["model"]]
                if trace_values.get("model")
                else preferred_models
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
                "citation_failures": final_citation_failures,
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
            max_sources=8,
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
            "status": "requires_review",
            "source_confidence": confidence,
            "quality_score": 0.0,
            "review_notes": "Awaiting evidence-grounded AI generation.",
            "metadata": {
                "source_types": sorted(source_types),
                "generator": "AIDiseaseBriefGenerator",
                "version": 1,
                "profile_schema": profile_schema.to_dict(),
                "target_sections": target_sections,
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
        return await asyncio.wait_for(
            agent.complete(
                prompt=prompt,
                system=system,
                use_cache=True,
                preferred_models=preferred_models,
                max_quota_recovery_rounds=0,
                wait_for_model_recovery=False,
                model_request_timeout_seconds=config.knowledge_model_request_timeout_seconds,
                max_attempts_per_model=config.knowledge_model_attempts_per_route,
                timeout_cooldown_seconds=config.knowledge_timeout_cooldown_seconds,
            ),
            timeout=float(config.knowledge_generation_timeout_seconds),
        )

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
            "route_timeout_seconds": config.knowledge_model_request_timeout_seconds,
            "timeout_cooldown_seconds": config.knowledge_timeout_cooldown_seconds,
            "output_repair_attempts": config.knowledge_output_repair_attempts,
            "quota_recovery_rounds": 0,
        }

    @staticmethod
    def _text_or_none(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    @staticmethod
    def _preferred_models_for(*, disease_id: str, language: str) -> tuple[list[str], int, str]:
        shard_models = list(get_config().ai.knowledge_model_shards)
        shard_key = f"{(disease_id or '').strip().upper()}:{language}"
        if not shard_models:
            return [], 0, shard_key

        digest = hashlib.md5(shard_key.encode("utf-8")).hexdigest()
        shard_index = int(digest[:8], 16) % len(shard_models)
        ordered = shard_models[shard_index:] + shard_models[:shard_index]
        return ordered, shard_index, shard_key

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
            "Citations are mandatory in every non-null field. Use only sequential citation_ref markers "
            "such as [1] or [1][2], immediately after the supported claim. Never cite database IDs.\n"
            "Write fluent scholarly prose in the payload's output_language. Return JSON only with "
            "exactly these keys: "
            "brief, definition, clinical_features, epidemiology, transmission, prevention, "
            "surveillance_note, risk_groups. Values are substantive strings or null. Prefer concise "
            "2–4 sentence sections; a brief may contain 2–5 sentences."
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
            sources[:8], profile_schema, target_sections=evidence_target_sections
        )
        source_payload = []
        for index, src in enumerate(sources[:8], start=1):
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
            "evidence_manifest": evidence_manifest.to_dict(
                include_text=True,
                include_source_ids=False,
                include_content_hashes=False,
            ),
            "sources": source_payload,
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
            "Generate only target_sections. Use evidence_manifest as the sole factual boundary; "
            "sources contains attribution labels only. If no allowed fragment supports a field, set it "
            "to null—never guess or add an absence explanation. JSON only.\n"
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
    def _json_format_repair_prompt(*, previous_response: str, language: str) -> str:
        return (
            "Convert the supplied model response into one valid JSON object with exactly these keys: "
            "brief, definition, clinical_features, epidemiology, transmission, prevention, "
            "surveillance_note, risk_groups. Preserve wording and citation markers; do not add facts. "
            "Use null for missing values and output JSON only. "
            f"Language: {language}. Response: {previous_response}"
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
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise
            parsed = json.loads(match.group(0))
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
