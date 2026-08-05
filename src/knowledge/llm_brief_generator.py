"""LLM-backed disease brief generation using the AI model center."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from src.ai.agents.base import BaseAgent
from src.core import get_config, get_logger
from src.knowledge.brief_generator import DISCLAIMER_EN, DISCLAIMER_ZH
from src.knowledge.citations import normalize_knowledge_citations
from src.knowledge.evidence import build_evidence_manifest
from src.knowledge.profile_schema import resolve_knowledge_profile_schema
from src.knowledge.quality import apply_knowledge_quality_gate

logger = get_logger(__name__)


class KnowledgeBriefAgent(BaseAgent):
    """Small concrete agent for schema-first knowledge brief generation."""

    def __init__(self) -> None:
        super().__init__(
            name="knowledge_brief_generator",
            temperature=0.2,
            max_tokens=4000,
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
        public_sources = self._usable_public_sources(sources)
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
        prompt = self._user_prompt(disease=disease, sources=public_sources, language=language)
        agent = self._spawn_agent()

        try:
            started_at = time.time()
            response = await agent.complete(
                prompt=prompt,
                system=system,
                use_cache=True,
                preferred_models=preferred_models,
                max_quota_recovery_rounds=0,
            )
            duration = time.time() - started_at
            latest_conversation = agent.get_latest_conversation() or {}
            parsed = self._parse_json(response)
        except Exception as exc:
            logger.warning("AI disease brief generation failed for %s/%s: %s", disease.get("disease_id"), language, exc)
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
                    "response": None,
                    "error": str(exc),
                    "cache_hit": False,
                },
            }

        token_usage = latest_conversation.get("tokens") if isinstance(latest_conversation.get("tokens"), dict) else {}
        model_used = self._text_or_none(latest_conversation.get("model"))
        provider_used = self._text_or_none(latest_conversation.get("provider"))
        actual_duration = float(latest_conversation.get("duration") or duration or 0.0)
        cache_hit = bool((latest_conversation.get("metadata") or {}).get("cache_hit")) if isinstance(latest_conversation.get("metadata"), dict) else False

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
            "source_attribution": scaffold["source_attribution"],
            "disclaimer": DISCLAIMER_ZH if language == "zh" else DISCLAIMER_EN,
            "model": model_used or "ai-model-center",
            "metadata": {
                **(scaffold.get("metadata") or {}),
                "generator": "AIDiseaseBriefGenerator",
                "ai_model": model_used,
                "ai_provider": provider_used,
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
                "token_usage": token_usage,
                "cache_hit": cache_hit,
                "version": 1,
                "profile_schema": profile_schema.to_dict(),
                "evidence_manifest": evidence_manifest.to_dict(),
                "target_sections": target_sections,
                "evidence_target_sections": evidence_target_sections,
            },
        }
        merged = normalize_knowledge_citations(merged, marker_mode="position")
        merged, assessment = apply_knowledge_quality_gate(merged)
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
                "model": model_used,
                "provider": provider_used,
                "token_usage": token_usage,
                "duration": actual_duration,
                "prompt": prompt,
                "system_prompt": system,
                "response": response,
                "error": None,
                "cache_hit": cache_hit,
            },
        }

    @classmethod
    def _usable_public_sources(cls, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        usable: list[dict[str, Any]] = []
        for source in sources:
            if str(source.get("source_type") or "") not in cls.PUBLIC_SOURCE_TYPES:
                continue
            if str(source.get("status") or "active") != "active":
                continue
            if str(source.get("review_status") or "pending") != "approved":
                continue
            metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
            try:
                if metadata.get("relevance_score") is not None and float(metadata["relevance_score"]) < 0.5:
                    continue
            except (TypeError, ValueError):
                continue
            if not any(
                source.get(field)
                for field in ("content_text", "content_sections", "raw_excerpt")
            ):
                continue
            usable.append(source)
        return usable[:8]

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

    def _spawn_agent(self) -> KnowledgeBriefAgent:
        if self.agent is not None:
            self.agent.clear_conversation_history()
            return self.agent
        return KnowledgeBriefAgent()

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
    def _system_prompt(language: str) -> str:
        output_language = "Chinese" if language == "zh" else "English"
        return (
            "You generate evidence-grounded public-health knowledge profiles for a surveillance website. "
            "The entity may be an infectious disease, classification scope, clinical syndrome/outcome, public-health intervention, outbreak, occupational condition, injury/poisoning event, or violence event. "
            "Knowledge brief schema version 4. "
            "Write in a scholarly public-health register, closer to an encyclopedia abstract or WHO fact note than to marketing copy or a chat response. "
            "Use ONLY the provided catalogue fields and source snippets/metadata. Do not add facts that are not supported. "
            "Every concrete detail must be directly supported by the snippets, parsed page text, or structured metadata; do not use outside medical knowledge. "
            "If the snippets do not support a field, return null for that field. Never fill a field with prose explaining that information is unavailable. "
            "Do not reuse boilerplate or placeholder phrases when the source material provides usable detail. "
            "Each non-null field should be a substantive paragraph, not a label, and the combined result should read like a serious academic disease profile. "
            "Use the profile_schema labels and applicability rules from the payload to interpret each stable storage field. "
            "Never generate content for not_applicable_fields. For classification scopes, describe inclusion boundaries instead of inventing clinical facts. "
            "For occupational, injury, poisoning, or violence entities, use exposure mechanism and health consequences rather than infectious-disease transmission language. "
            "Every evidence fragment may be used only for its supported_sections. Fragments with an empty supported_sections list are not evidence for any output field. "
            "Inherited evidence fragments are additionally restricted to their explicit allowed sections; they cannot support subtype-specific course, burden, or population claims. "
            "Do not provide diagnosis, treatment, dosing, or personal medical advice. "
            "Summarize in your own words and avoid copying long source passages. "
            "\n\n"
            "INLINE CITATIONS: You MUST insert inline citation markers in the format [n], where n is the sequential citation_ref shown in the sources array. "
            "Do not use the database source_id/id as a citation number. "
            "Only use citation_ref values that are present in the provided sources array, normally [1], [2], [3], etc. "
            "Insert the marker immediately after each factual claim or data point that comes from a specific source. "
            "Place the citation marker at the end of the sentence or clause containing the claim, before the period. "
            "Multiple citations for the same claim should be written as [n1][n2]. "
            "Every substantive paragraph should have at least one citation. "
            "Example: 'The disease has a case-fatality rate of 30-60% [2]. It is transmitted primarily through respiratory droplets [1][3].' "
            "\n\n"
            f"Write in {output_language}. Return one valid JSON object only with keys: "
            "brief, definition, clinical_features, epidemiology, transmission, prevention, surveillance_note, risk_groups. "
            "Each value must be either a plain string suitable for public-health surveillance interpretation or null when evidence is insufficient. "
            "Target length: brief 2-5 sentences; definition 2-4 sentences; clinical_features 3-5 sentences; epidemiology 3-5 sentences; transmission/prevention/surveillance_note 2-4 sentences each. "
            "Prefer a readable public-health profile over a terse dictionary entry. "
            "If headings or page structure are available in the source payload, use them to organize the prose without quoting them verbatim. "
            "For Chinese output, write natural Chinese prose without leaving English fallback phrases unless the source explicitly uses them."
        )

    @staticmethod
    def _user_prompt(*, disease: dict[str, Any], sources: list[dict[str, Any]], language: str) -> str:
        profile_schema = resolve_knowledge_profile_schema(disease)
        target_sections = (
            list(disease.get("target_sections") or [])
            if "target_sections" in disease
            else list(profile_schema.required_fields)
        )
        evidence_target_sections = list(
            disease.get("evidence_target_sections") or target_sections or profile_schema.required_fields
        )
        evidence_manifest = build_evidence_manifest(
            sources[:8],
            profile_schema,
            target_sections=evidence_target_sections,
        )
        source_payload = []
        for index, src in enumerate(sources[:8], start=1):
            source_metadata = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
            source_payload.append(
                {
                    "citation_ref": index,
                    "source_id": src.get("id"),
                    "source_type": src.get("source_type"),
                    "source_name": src.get("source_name"),
                    "title": src.get("title"),
                    "url": src.get("url"),
                    "resolved_url": src.get("resolved_url"),
                    "license": src.get("license"),
                    "review_status": src.get("review_status"),
                    "inherited_from_disease_id": source_metadata.get("inherited_from_disease_id"),
                    "allowed_sections": source_metadata.get("allowed_sections"),
                }
            )
        payload = {
            "language": language,
            "disease": {
                "disease_id": disease.get("disease_id"),
                "name_en": disease.get("name_en") or disease.get("standard_name_en"),
                "name_zh": disease.get("name_zh") or disease.get("standard_name_zh"),
                "category": disease.get("category"),
                "description": disease.get("description"),
                "icd_10": disease.get("icd_10"),
                "icd_11": disease.get("icd_11"),
                "ontology_context": disease.get("ontology_context") or {},
            },
            "profile_schema": profile_schema.to_dict(),
            "target_sections": target_sections,
            "evidence_manifest": evidence_manifest.to_dict(include_text=True),
            "sources": source_payload,
        }
        return (
            "Create a high-value but conservative public-health profile from this JSON payload. "
            "Keep the tone formal, clinical, and academically useful. "
            "The evidence_manifest is the only factual evidence boundary; the sources array is attribution metadata only. "
            "Use a fragment only for the fields listed in its supported_sections. If a field has no supporting fragment, set it to null instead of guessing or writing an absence explanation. "
            "Generate only target_sections (plus brief when it is targeted); return null for other fields so already-published sections can remain locked. "
            "Use the same evidence_manifest fragments and citation references for both language runs. "
            "Do not add facts from general memory. "
            "Write in fluent, readable prose that gives the page enough substance to be useful at a glance.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
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
