"""LLM-backed disease brief generation using the AI model center."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from src.ai.agents.base import BaseAgent
from src.core import get_config, get_logger
from src.knowledge.brief_generator import DISCLAIMER_EN, DISCLAIMER_ZH, SourceGroundedBriefGenerator
from src.knowledge.citations import normalize_knowledge_citations

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

    def __init__(self, agent: KnowledgeBriefAgent | None = None) -> None:
        self.agent = agent
        self.template_generator = SourceGroundedBriefGenerator()

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
        baseline = self.template_generator.generate(disease=disease, sources=sources, language=language)
        usable_sources = self.template_generator._usable_sources(sources)
        public_sources = [src for src in usable_sources if str(src.get("source_type") or "") != "msd"]
        preferred_models, shard_index, shard_key = self._preferred_models_for(
            disease_id=str(disease.get("disease_id") or ""),
            language=language,
        )
        if not public_sources:
            return {
                "payload": baseline,
                "trace": {
                    "generator": "template",
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
                    "error": None,
                    "cache_hit": False,
                },
            }

        source_ids = [src.get("id") for src in public_sources if src.get("id") is not None]
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
            )
            duration = time.time() - started_at
            latest_conversation = agent.get_latest_conversation() or {}
            parsed = self._parse_json(response)
        except Exception as exc:
            logger.warning("AI disease brief generation failed for %s/%s: %s", disease.get("disease_id"), language, exc)
            baseline["review_notes"] = f"{baseline.get('review_notes') or ''}; AI generation failed: {exc}".strip("; ")
            baseline["metadata"] = {
                **(baseline.get("metadata") or {}),
                "ai_generation_failed": str(exc),
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
            }
            return {
                "payload": baseline,
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

        merged = {
            **baseline,
            "brief": self._field(parsed, "brief", baseline["brief"]),
            "definition": self._field(parsed, "definition", baseline["definition"]),
            "clinical_features": self._field(parsed, "clinical_features", baseline["clinical_features"]),
            "epidemiology": self._field(parsed, "epidemiology", baseline["epidemiology"]),
            "transmission": self._field(parsed, "transmission", baseline["transmission"]),
            "prevention": self._field(parsed, "prevention", baseline["prevention"]),
            "surveillance_note": self._field(parsed, "surveillance_note", baseline["surveillance_note"]),
            "clinical_summary": self._field(parsed, "clinical_summary", baseline["clinical_summary"] or baseline["clinical_features"]),
            "risk_groups": self._field(parsed, "risk_groups", baseline["risk_groups"]),
            "source_ids": source_ids,
            "source_attribution": baseline["source_attribution"],
            "disclaimer": DISCLAIMER_ZH if language == "zh" else DISCLAIMER_EN,
            "model": model_used or "ai-model-center",
            "metadata": {
                **(baseline.get("metadata") or {}),
                "generator": "AIDiseaseBriefGenerator",
                "ai_model": model_used,
                "ai_provider": provider_used,
                "preferred_models": preferred_models,
                "shard_index": shard_index,
                "shard_key": shard_key,
                "token_usage": token_usage,
                "cache_hit": cache_hit,
                "version": 1,
            },
        }
        merged = normalize_knowledge_citations(merged, marker_mode="position")
        validation = self.template_generator.validate(merged)
        if not validation.ok:
            merged["status"] = "requires_review"
            merged["quality_score"] = min(float(merged.get("quality_score") or 0.5), 0.5)
            merged["review_notes"] = "; ".join(validation.issues)
        elif merged.get("status") == "published":
            merged["quality_score"] = max(float(merged.get("quality_score") or 0.0), 0.9)
            merged["review_notes"] = "AI-generated, source-grounded brief; ready for human spot review."
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
            "You generate detailed infectious-disease knowledge briefs for a surveillance website. "
            "Knowledge brief schema version 2. "
            "Write in a scholarly public-health register, closer to an encyclopedia abstract or WHO fact note than to marketing copy or a chat response. "
            "Use ONLY the provided catalogue fields and source snippets/metadata. Do not add facts that are not supported. "
            "Every concrete detail must be directly supported by the snippets, parsed page text, or structured metadata; do not use outside medical knowledge. "
            "If the snippets do not state a detail such as timing, vaccine schedule, transmission persistence, severity pattern, or specific high-risk groups, omit it or say source-backed detail is not yet available. "
            "Do not reuse boilerplate or placeholder phrases when the source material provides usable detail. "
            "Each field should be a substantive paragraph, not a label, and the combined result should read like a serious academic disease profile. "
            "Treat the fields as follows: definition = disease identity and etiologic characterization; clinical_features = syndrome, severity, course, and complications; epidemiology = geographic distribution, outbreak context, reservoir or exposure ecology, and surveillance burden; transmission = route or exposure mechanism; prevention = public-health or exposure-control measures; surveillance_note = how to read the disease in monitoring context. "
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
            "Each value must be a plain string suitable for public-health surveillance interpretation. "
            "Target length: brief 2-5 sentences; definition 2-4 sentences; clinical_features 3-5 sentences; epidemiology 3-5 sentences; transmission/prevention/surveillance_note 2-4 sentences each. "
            "Prefer a readable public-health profile over a terse dictionary entry. "
            "If headings or page structure are available in the source payload, use them to organize the prose without quoting them verbatim. "
            "For Chinese output, write natural Chinese prose without leaving English fallback phrases unless the source explicitly uses them."
        )

    @staticmethod
    def _user_prompt(*, disease: dict[str, Any], sources: list[dict[str, Any]], language: str) -> str:
        source_payload = []
        for index, src in enumerate(sources[:8], start=1):
            content_text = src.get("content_text") or src.get("raw_excerpt") or src.get("snippet") or src.get("description") or ""
            sections = src.get("content_sections") or []
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
                    "content_text": _clip(content_text, 1500),
                    "content_sections": sections[:6],
                    "snippet": _clip(src.get("raw_excerpt") or src.get("snippet") or src.get("description") or "", 500),
                    "review_status": src.get("review_status"),
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
            },
            "sources": source_payload,
        }
        return (
            "Create a high-value but conservative disease profile from this JSON payload. "
            "Keep the tone formal, clinical, and academically useful. "
            "Use the source snippets as the evidence boundary. If a field is not supported by the snippets, say that source-backed detail is not yet available instead of guessing. "
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
    def _field(payload: dict[str, Any], key: str, fallback: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return fallback
        return " ".join(value.split())


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
