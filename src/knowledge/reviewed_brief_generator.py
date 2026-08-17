"""Generate briefs from explicitly reviewed, source-cited profile bundles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.knowledge.brief_generator import DISCLAIMER_EN, DISCLAIMER_ZH
from src.knowledge.citations import normalize_knowledge_citations
from src.knowledge.evidence import build_evidence_manifest
from src.knowledge.llm_brief_generator import AIDiseaseBriefGenerator
from src.knowledge.profile_schema import resolve_knowledge_profile_schema
from src.knowledge.quality import apply_knowledge_quality_gate


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEWED_PROFILES_PATH = ROOT / "configs" / "knowledge_reviewed_profiles.json"


class ReviewedDiseaseBriefGenerator:
    """Render a curator-reviewed profile without requiring a model call."""

    def __init__(self, path: Path = DEFAULT_REVIEWED_PROFILES_PATH) -> None:
        self.path = path
        self._document = self._load_document(path)

    @staticmethod
    def _load_document(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": 1, "diseases": {}}
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        if document.get("schema_version") != 1:
            raise ValueError("reviewed knowledge profile schema_version must be 1")
        if not isinstance(document.get("diseases"), dict):
            raise ValueError("reviewed knowledge profiles must contain a diseases object")
        return document

    def has_profile(self, disease_id: str) -> bool:
        entry = self._document["diseases"].get(str(disease_id or "").upper())
        return bool(
            isinstance(entry, dict)
            and isinstance(entry.get("profiles"), dict)
            and all(isinstance(entry["profiles"].get(language), dict) for language in ("en", "zh"))
        )

    async def generate_with_trace(
        self,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
    ) -> dict[str, Any]:
        language = "zh" if language == "zh" else "en"
        disease_id = str(disease.get("disease_id") or "").upper()
        entry = self._document["diseases"].get(disease_id)
        profile = entry.get("profiles", {}).get(language) if isinstance(entry, dict) else None
        profile_schema = resolve_knowledge_profile_schema(disease)
        target_sections = (
            list(disease.get("target_sections") or [])
            if "target_sections" in disease
            else ["brief", *profile_schema.required_fields]
        )
        ordered_urls = entry.get("source_urls") if isinstance(entry, dict) else []
        sources_by_url: dict[str, dict[str, Any]] = {}
        for source in sources:
            for url in (source.get("url"), source.get("resolved_url")):
                if url:
                    sources_by_url[str(url)] = source
        selected_sources = [
            sources_by_url[url]
            for url in ordered_urls or []
            if url in sources_by_url
        ]
        if not selected_sources:
            selected_sources = AIDiseaseBriefGenerator._usable_public_sources(
                sources,
                disease=disease,
            )

        scaffold = AIDiseaseBriefGenerator._empty_scaffold(
            disease=disease,
            sources=selected_sources,
            language=language,
            profile_schema=profile_schema,
            target_sections=target_sections,
        )
        if not isinstance(profile, dict) or not selected_sources:
            reason = (
                "No reviewed profile is configured"
                if not isinstance(profile, dict)
                else "Reviewed profile sources were not available"
            )
            return {
                "payload": scaffold,
                "trace": self._trace(language=language, error=reason),
            }

        section_fields = (
            "brief",
            "definition",
            "clinical_features",
            "epidemiology",
            "transmission",
            "prevention",
            "surveillance_note",
            "risk_groups",
        )
        values = {
            field: profile.get(field) if field in target_sections else None
            for field in section_fields
        }
        evidence_manifest = build_evidence_manifest(
            selected_sources,
            profile_schema,
            target_sections=(
                disease.get("evidence_target_sections")
                or target_sections
                or profile_schema.required_fields
            ),
        )
        payload = {
            **scaffold,
            **values,
            "clinical_summary": values["clinical_features"],
            "disclaimer": DISCLAIMER_ZH if language == "zh" else DISCLAIMER_EN,
            "model": "reviewed-profile-v1",
            "status": "published",
            "source_confidence": "high",
            "review_notes": "Curator-reviewed, source-cited profile.",
            "metadata": {
                **(scaffold.get("metadata") or {}),
                "generator": "ReviewedDiseaseBriefGenerator",
                "reviewed_profile_version": entry.get("version", 1),
                "pipeline_version": 2,
                "profile_schema": profile_schema.to_dict(),
                "target_sections": target_sections,
                "source_urls": ordered_urls or [],
                "evidence_manifest": evidence_manifest.to_dict(),
            },
        }
        payload = normalize_knowledge_citations(
            payload,
            marker_mode="position",
            prune_uncited_sources=True,
        )
        payload, assessment = apply_knowledge_quality_gate(payload)
        error = None
        if payload.get("status") != "published" or assessment.missing_required_fields:
            error = "; ".join(assessment.issues) or "reviewed profile failed quality gate"
        return {
            "payload": payload,
            "trace": self._trace(language=language, error=error),
        }

    @staticmethod
    def _trace(*, language: str, error: str | None) -> dict[str, Any]:
        return {
            "generator": "reviewed",
            "language": language,
            "model": None,
            "provider": None,
            "token_usage": {},
            "duration": 0.0,
            "prompt": None,
            "system_prompt": None,
            "response": None,
            "error": error,
            "cache_hit": False,
        }
