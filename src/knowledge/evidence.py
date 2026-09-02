"""Shared evidence-fragment manifests for bilingual knowledge generation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from src.knowledge.profile_schema import KnowledgeProfileSchema


_SECTION_KEYWORDS = {
    "definition": (
        "definition", "cause", "caused by", "etiology", "pathogen", "infection",
        "定义", "病因", "病原", "感染",
    ),
    "clinical_features": (
        "symptom", "signs", "clinical", "complication", "severity", "illness",
        "症状", "体征", "临床", "并发症", "病情",
    ),
    "epidemiology": (
        "epidemiology", "burden", "outbreak", "incidence", "prevalence", "distribution",
        "reported cases", "endemic", "流行", "暴发", "负担", "发病", "分布",
    ),
    "transmission": (
        "transmission", "spread", "exposure", "route", "vector", "contact", "borne",
        "传播", "扩散", "暴露", "媒介", "接触",
    ),
    "prevention": (
        "prevention", "control", "protect", "vaccine", "vaccination", "immunization",
        "prophylaxis", "hygiene", "预防", "控制", "疫苗", "免疫", "卫生",
    ),
    "surveillance_note": (
        "surveillance", "case definition", "reporting", "notification", "laboratory testing",
        "监测", "病例定义", "报告", "通报", "实验室检测",
    ),
    "risk_groups": (
        "risk group", "at risk", "risk of", "vulnerable", "susceptible", "population",
        "occupation", "高风险", "风险人群", "重点人群", "易感", "职业",
    ),
}

MAX_EVIDENCE_MANIFEST_CHARACTERS = 18_000


@dataclass(frozen=True)
class EvidenceFragment:
    fragment_id: str
    citation_ref: int
    source_id: int | str | None
    heading: str | None
    text: str
    supported_sections: tuple[str, ...]
    inherited_from_disease_id: str | None

    def to_dict(
        self,
        *,
        include_text: bool,
        include_source_id: bool = True,
        include_content_hash: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "fragment_id": self.fragment_id,
            "citation_ref": self.citation_ref,
            "heading": self.heading,
            "supported_sections": list(self.supported_sections),
            "inherited_from_disease_id": self.inherited_from_disease_id,
        }
        if include_source_id:
            payload["source_id"] = self.source_id
        if include_content_hash:
            payload["content_hash"] = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if include_text:
            payload["text"] = self.text
        return payload


@dataclass(frozen=True)
class EvidenceManifest:
    manifest_id: str
    target_sections: tuple[str, ...]
    fragments: tuple[EvidenceFragment, ...]

    def to_dict(
        self,
        *,
        include_text: bool = False,
        include_source_ids: bool = True,
        include_content_hashes: bool = True,
    ) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "target_sections": list(self.target_sections),
            "fragments": [
                fragment.to_dict(
                    include_text=include_text,
                    include_source_id=include_source_ids,
                    include_content_hash=include_content_hashes,
                )
                for fragment in self.fragments
            ],
        }


@dataclass(frozen=True)
class EvidencePacket:
    """The exact, quality-assessed evidence boundary sent to generation."""

    sources: tuple[dict[str, Any], ...]
    manifest: EvidenceManifest
    assessment: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_count": len(self.sources),
            "source_ids": [source.get("id") for source in self.sources],
            "manifest": self.manifest.to_dict(),
            "assessment": self.assessment.to_dict(),
        }


def build_evidence_manifest(
    sources: Iterable[dict[str, Any]],
    profile_schema: KnowledgeProfileSchema,
    *,
    target_sections: Iterable[str] = (),
    max_total_characters: int = MAX_EVIDENCE_MANIFEST_CHARACTERS,
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
        for section in sections:
            if not isinstance(section, dict):
                continue
            text = _compact(section.get("text") or section.get("content") or section.get("body"))
            if text:
                raw_fragments.append((_compact(section.get("heading")) or None, text))
        ranked_fragments: list[tuple[int, int, str | None, str]] = []
        seen_fragment_texts: set[str] = set()
        for original_index, (heading, text) in enumerate(raw_fragments):
            text_key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if text_key in seen_fragment_texts:
                continue
            seen_fragment_texts.add(text_key)
            inferred = set(_infer_sections(f"{heading or ''} {text[:600]}", profile_schema))
            coverage = len(inferred & (set(targets) - {"brief"}))
            ranked_fragments.append((coverage, -original_index, heading, text))
        ranked_fragments.sort(reverse=True, key=lambda item: (item[0], item[1]))

        for index, (_coverage, _position, heading, text) in enumerate(ranked_fragments[:4], start=1):
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

    fragments = _limit_fragment_characters(
        fragments,
        max_total_characters=max_total_characters,
    )
    identity = "|".join(
        (
            f"{item.fragment_id}:{item.citation_ref}:{item.inherited_from_disease_id}:"
            f"{','.join(item.supported_sections)}:"
            f"{hashlib.sha256(item.text.encode('utf-8')).hexdigest()}"
        )
        for item in fragments
    )
    manifest_id = hashlib.sha256(
        f"{profile_schema.profile_type}|{','.join(targets)}|{identity}".encode("utf-8")
    ).hexdigest()
    return EvidenceManifest(manifest_id, targets, tuple(fragments))


def prepare_evidence_packet(
    sources: Iterable[dict[str, Any]],
    profile_schema: KnowledgeProfileSchema,
    *,
    target_sections: Iterable[str] = (),
    max_sources: int = 8,
    max_manifest_characters: int = MAX_EVIDENCE_MANIFEST_CHARACTERS,
    allowed_source_types: set[str] | frozenset[str] | None = None,
) -> EvidencePacket:
    """Select, deduplicate and assess the exact source packet used by models."""
    from src.knowledge.quality import (
        _canonical_source_url,
        _unique_grounding_text,
        assess_knowledge_evidence,
        has_grounding_content,
    )

    targets = tuple(target_sections) or tuple(profile_schema.required_fields)
    source_weights = {
        "who": 100,
        "who_don": 96,
        "web_search": 86,
        "pubmed": 82,
        "wikipedia": 70,
        "wikidata": 58,
        "msd": 20,
    }
    candidates: list[tuple[float, set[str], dict[str, Any], str, str]] = []
    for source in sources:
        source_type = str(source.get("source_type") or "").strip().lower()
        if allowed_source_types is not None and source_type not in allowed_source_types:
            continue
        if str(source.get("status") or "active").strip().lower() != "active":
            continue
        if str(source.get("review_status") or "pending").strip().lower() != "approved":
            continue
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        try:
            relevance = float(metadata.get("relevance_score", 1.0))
        except (TypeError, ValueError):
            continue
        if relevance < 0.5 or not has_grounding_content(source):
            continue
        canonical_url = _canonical_source_url(source)
        evidence_text = _unique_grounding_text(source)
        content_hash = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
        manifest = build_evidence_manifest(
            [source], profile_schema, target_sections=targets
        )
        coverage = {
            field
            for fragment in manifest.fragments
            for field in fragment.supported_sections
            if field in targets
        }
        authority_bonus = 12 if source_type in {"who", "who_don"} else 0
        score = (
            source_weights.get(source_type, 30)
            + authority_bonus
            + relevance * 10
            + min(len(evidence_text), 4000) / 1000
        )
        candidates.append((score, coverage, source, canonical_url, content_hash))

    # Prefer the richest representation when several adapters resolve to the
    # same page or return byte-identical evidence.
    candidates.sort(
        key=lambda item: (item[0], item[3], item[4]),
        reverse=True,
    )
    deduplicated: list[tuple[float, set[str], dict[str, Any], str, str]] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    for candidate in candidates:
        _score, _coverage, _source, canonical_url, content_hash = candidate
        if canonical_url and canonical_url in seen_urls:
            continue
        if content_hash in seen_hashes:
            continue
        if canonical_url:
            seen_urls.add(canonical_url)
        seen_hashes.add(content_hash)
        deduplicated.append(candidate)

    selected: list[dict[str, Any]] = []
    uncovered = set(targets)
    remaining = list(deduplicated)
    while remaining and len(selected) < max_sources and uncovered:
        best = max(
            remaining,
            key=lambda item: (
                len(item[1] & uncovered),
                item[0],
                item[3],
                item[4],
            ),
        )
        if not (best[1] & uncovered):
            break
        selected.append(best[2])
        uncovered -= best[1]
        remaining.remove(best)
    for candidate in remaining:
        if len(selected) >= max_sources:
            break
        selected.append(candidate[2])

    manifest = build_evidence_manifest(
        selected,
        profile_schema,
        target_sections=targets,
        max_total_characters=max_manifest_characters,
    )
    assessment = assess_knowledge_evidence(selected)
    return EvidencePacket(tuple(selected), manifest, assessment)


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


def _limit_fragment_characters(
    fragments: list[EvidenceFragment],
    *,
    max_total_characters: int,
) -> list[EvidenceFragment]:
    """Apply a balanced prompt budget without starving later sources."""
    if max_total_characters <= 0:
        return []
    if sum(len(fragment.text) for fragment in fragments) <= max_total_characters:
        return fragments

    by_source: dict[int, list[EvidenceFragment]] = {}
    source_order: list[int] = []
    for fragment in fragments:
        if fragment.citation_ref not in by_source:
            by_source[fragment.citation_ref] = []
            source_order.append(fragment.citation_ref)
        by_source[fragment.citation_ref].append(fragment)

    selected: list[EvidenceFragment] = []
    used = 0
    max_rounds = max((len(items) for items in by_source.values()), default=0)
    for round_index in range(max_rounds):
        for citation_ref in source_order:
            source_fragments = by_source[citation_ref]
            if round_index >= len(source_fragments):
                continue
            fragment = source_fragments[round_index]
            if used + len(fragment.text) > max_total_characters:
                continue
            selected.append(fragment)
            used += len(fragment.text)
    return selected


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()
