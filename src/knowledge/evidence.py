"""Shared evidence-fragment manifests for bilingual knowledge generation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from src.knowledge.profile_schema import KnowledgeProfileSchema


_SECTION_KEYWORDS = {
    "definition": ("definition", "cause", "etiology", "pathogen", "定义", "病因", "病原"),
    "clinical_features": ("symptom", "clinical", "complication", "severity", "症状", "临床", "并发症"),
    "epidemiology": ("epidemiology", "burden", "outbreak", "incidence", "prevalence", "流行", "暴发", "负担"),
    "transmission": ("transmission", "exposure", "route", "vector", "传播", "暴露", "媒介"),
    "prevention": ("prevention", "control", "vaccine", "prophylaxis", "预防", "控制", "疫苗"),
    "surveillance_note": ("surveillance", "case definition", "reporting", "监测", "病例定义", "报告"),
    "risk_groups": ("risk group", "vulnerable", "population", "occupation", "高风险", "重点人群", "职业"),
}


@dataclass(frozen=True)
class EvidenceFragment:
    fragment_id: str
    citation_ref: int
    source_id: int | str | None
    heading: str | None
    text: str
    supported_sections: tuple[str, ...]
    inherited_from_disease_id: str | None

    def to_dict(self, *, include_text: bool) -> dict[str, Any]:
        payload = {
            "fragment_id": self.fragment_id,
            "citation_ref": self.citation_ref,
            "source_id": self.source_id,
            "heading": self.heading,
            "supported_sections": list(self.supported_sections),
            "inherited_from_disease_id": self.inherited_from_disease_id,
            "content_hash": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }
        if include_text:
            payload["text"] = self.text
        return payload


@dataclass(frozen=True)
class EvidenceManifest:
    manifest_id: str
    target_sections: tuple[str, ...]
    fragments: tuple[EvidenceFragment, ...]

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "target_sections": list(self.target_sections),
            "fragments": [fragment.to_dict(include_text=include_text) for fragment in self.fragments],
        }


def build_evidence_manifest(
    sources: Iterable[dict[str, Any]],
    profile_schema: KnowledgeProfileSchema,
    *,
    target_sections: Iterable[str] = (),
) -> EvidenceManifest:
    """Build one immutable evidence boundary shared by both languages."""
    targets = tuple(
        field for field in target_sections if field == "brief" or field in profile_schema.applicable_fields
    ) or tuple(profile_schema.required_fields)
    fragments: list[EvidenceFragment] = []
    for citation_ref, source in enumerate(sources, start=1):
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        inherited_from = str(metadata.get("inherited_from_disease_id") or "").strip() or None
        allowed = {
            str(field) for field in metadata.get("allowed_sections") or profile_schema.applicable_fields
        }
        sections = source.get("content_sections") if isinstance(source.get("content_sections"), list) else []
        raw_fragments: list[tuple[str | None, str]] = []
        overview_text = _compact(source.get("content_text") or source.get("raw_excerpt"))
        if overview_text:
            raw_fragments.append((None, overview_text))
        for section in sections[:8]:
            if not isinstance(section, dict):
                continue
            text = _compact(section.get("text") or section.get("content") or section.get("body"))
            if text:
                raw_fragments.append((_compact(section.get("heading")) or None, text))
        for index, (heading, text) in enumerate(raw_fragments[:8], start=1):
            supported = set(_infer_sections(f"{heading or ''} {text[:600]}", profile_schema))
            if inherited_from:
                supported &= allowed
            inferred_supported = set(supported)
            if targets:
                supported &= set(targets) - {"brief"}
            # A direct source fragment can ground a lead summary when it has
            # substantive section support. Parent evidence never gains this
            # permission implicitly.
            if not inherited_from and "brief" in targets and inferred_supported:
                supported.add("brief")
            fragments.append(
                EvidenceFragment(
                    fragment_id=f"E{citation_ref}.{index}",
                    citation_ref=citation_ref,
                    source_id=source.get("id") or source.get("source_id"),
                    heading=heading,
                    text=text[:1800],
                    supported_sections=tuple(
                        field for field in ("brief", *profile_schema.applicable_fields) if field in supported
                    ),
                    inherited_from_disease_id=inherited_from,
                )
            )

    identity = "|".join(
        (
            f"{item.fragment_id}:{item.source_id}:{item.inherited_from_disease_id}:"
            f"{','.join(item.supported_sections)}:"
            f"{hashlib.sha256(item.text.encode('utf-8')).hexdigest()}"
        )
        for item in fragments
    )
    manifest_id = hashlib.sha256(
        f"{profile_schema.profile_type}|{','.join(targets)}|{identity}".encode("utf-8")
    ).hexdigest()
    return EvidenceManifest(manifest_id, targets, tuple(fragments))


def _infer_sections(text: str, profile_schema: KnowledgeProfileSchema) -> tuple[str, ...]:
    lowered = text.lower()
    matched = [
        field
        for field, keywords in _SECTION_KEYWORDS.items()
        if field in profile_schema.applicable_fields and any(keyword in lowered for keyword in keywords)
    ]
    # Unheaded general source text remains usable for definition/overview only;
    # it cannot silently support specialized transmission or prevention claims.
    if not matched and "definition" in profile_schema.applicable_fields:
        matched.append("definition")
    return tuple(matched)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()
