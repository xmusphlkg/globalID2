"""Semantic quality checks for public disease knowledge briefs.

The database schema intentionally allows most knowledge fields to be nullable.
This module preserves that distinction all the way to the public site: an
explicit statement that evidence is unavailable is useful review metadata, but
it is not disease knowledge and must not be counted as a completed field.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.knowledge.profile_schema import (
    SECTION_FIELDS,
    profile_schema_from_payload,
    resolve_knowledge_profile_schema,
)


KNOWLEDGE_TEXT_FIELDS = (
    "brief",
    "definition",
    "clinical_features",
    "epidemiology",
    "transmission",
    "prevention",
    "surveillance_note",
    "risk_groups",
)
PROFILE_SECTION_FIELDS = SECTION_FIELDS

KNOWLEDGE_SCHEMA_VERSION = 6
EVIDENCE_POLICY_VERSION = 4
KNOWLEDGE_PUBLICATION_MIN_QUALITY_SCORE = 0.85
QUALITY_REVIEW_NOTE_READY = "AI-generated, source-grounded brief; ready for human spot review."
QUALITY_REVIEW_NOTE_PARTIAL_PREFIX = (
    "AI-generated partial brief; unsupported fields were omitted and remain queued for enrichment."
)
QUALITY_REVIEW_NOTE_BLOCKED_PREFIX = "AI-generated brief requires review."

FIELD_STATUS_AVAILABLE = "available"
FIELD_STATUS_MISSING = "missing"
FIELD_STATUS_INSUFFICIENT = "insufficient_evidence"
FIELD_STATUS_LANGUAGE_MISMATCH = "language_mismatch"
FIELD_STATUS_NOT_APPLICABLE = "not_applicable"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？；;])\s*|\n+")
_CITATION_RE = re.compile(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# These patterns identify a sentence whose main purpose is to explain that the
# requested fact is absent.  They are deliberately sentence-scoped: a useful
# paragraph may contain one limitation sentence without becoming unavailable.
_UNAVAILABLE_EN_RE = re.compile(
    r"(?:"
    r"not\s+(?:yet\s+)?available|"
    r"no\s+source[- ]backed\s+(?:detail|description|information)|"
    r"(?:sources?|snippets?|materials?|payload|evidence|records?|excerpts?|metadata)\b.{0,80}?"
    r"(?:do\s+not|does\s+not|did\s+not|cannot|can't|lack)\s+"
    r"(?:provide|describe|identify|include|support|state|supply|contain|define|report|specify|establish)|"
    r"no\s+(?:population[- ]level\s+)?(?:incidence|prevalence|burden)"
    r"(?:\s+or\s+(?:incidence|prevalence|burden))?"
    r"(?:\s+(?:estimate|data|detail|information))?\s+"
    r"(?:is|are)\s+(?:present|available|provided|described|stated|identified|reported|specified)|"
    r"cannot\s+be\s+(?:stated|confirmed|inferred|determined)|"
    r"no\s+.{0,180}(?:can\s+be\s+(?:stated|confirmed|inferred)|"
    r"(?:is|are)\s+(?:available|provided|described|stated|identified|reported|specified))|"
    r"(?:is|are)\s+not\s+(?:available|provided|described|stated|identified|reported|specified)|"
    r"considered\s+unavailable|"
    r"would\s+need\s+to\s+come\s+from|"
    r"insufficient\s+(?:evidence|detail|information)|"
    r"would\s+be\s+speculative|"
    r"does\s+not\s+infer|"
    r"should\s+remain\s+review[- ]gated|"
    r"read\s+as\s+a\s+placeholder"
    r")",
    re.IGNORECASE,
)
_METADATA_ONLY_EN_RE = re.compile(
    r"(?:article|paper|publication|review)\s+title|"
    r"(?:scholarly|bibliographic|publication)\s+(?:metadata|attention|record)|"
    r"available\s+metadata\s+includes?|"
    r"(?:available\s+)?records?\s+are\s+scholarly\s+citations|"
    r"(?:records?|sources?)\s+(?:mainly\s+)?(?:point|refer)\s+to\s+(?:review\s+)?literature|"
    r"topic\s+(?:has\s+generated|was\s+addressed|is\s+a\s+recognized\s+topic)",
    re.IGNORECASE,
)
_UNAVAILABLE_ZH_RE = re.compile(
    r"(?:"
    r"(?:尚未|未能|无法|不能|不足以|不宜|尚无|暂无|未检出|未明确|未系统)"
    r".{0,28}(?:提供|形成|支持|确认|描述|推断|列出|摘要|信息|细节|结论)|"
    r"(?:来源|材料|片段|证据|资料|信息).{0,20}"
    r"(?:不足|缺失|有限|未提供|未描述|未给出|未明确|无法)|"
    r"不作推断|不能据此|未作说明|未包含|尚未可得|尚不可用|"
    r"(?:源)?依据不足|尚?缺乏.{0,16}(?:支持|依据|信息|细节|证据)|"
    r"尚未\s*(?:可得|可用|获得|提供|具备|[\u0900-\u097f]{2,})|"
    r"尚未.{0,16}(?:获得|得到).{0,8}支持|"
    r"(?:没有|无).{0,20}(?:资料|证据|信息).{0,20}(?:说明|支持|提供|描述|表明)|"
    r"(?:来源|材料|片段|证据|资料|信息).{0,40}(?:没有给出|没有提供|没有描述)|"
    r"不能仅凭.{0,30}(?:判断|推断|确认)|"
    r"不能从.{0,30}(?:导出|提炼|得出)|不应(?:据此)?外推|"
    r"适合用于.{0,20}(?:检索|证据汇编)|需进一步补充.{0,30}(?:条目|资料|证据)|"
    r"不是已具备.{0,30}(?:疾病概述|画像|档案)|"
    r"需(?:要|依赖).{0,16}(?:补充|支持|核验|来源)"
    r"|尚未充分可得|(?:源文)?(?:细节|信息).{0,10}(?:尚)?不足|"
    r"仅(?:能)?表明.{0,50}(?:未提供|不足)|并未.{0,30}(?:给出|提供|描述)|"
    r"建议标注|未证实"
    r")"
)
_METADATA_ONLY_ZH_RE = re.compile(
    r"(?:论文|文章|文献)(?:题名|题录|标题|条目)|(?:来源)?(?:题名|题录|标题)|"
    r"(?:学术|专业|既往|持续)(?:研究)?(?:关注|焦点)|"
    r"(?:研究方向|研究脉络|综述性文献|专题文献|专题讨论|研究相关|研究语境)"
)


@dataclass(frozen=True)
class KnowledgeFieldAssessment:
    """Quality classification for one localized text field."""

    status: str
    reason: str | None
    sentence_count: int
    unavailable_sentence_count: int

    @property
    def available(self) -> bool:
        return self.status == FIELD_STATUS_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available": self.available,
            "reason": self.reason,
            "sentence_count": self.sentence_count,
            "unavailable_sentence_count": self.unavailable_sentence_count,
        }


@dataclass(frozen=True)
class KnowledgeBriefAssessment:
    """Field coverage and publication decision for one localized brief."""

    language: str
    fields: dict[str, KnowledgeFieldAssessment]
    available_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    insufficient_fields: tuple[str, ...]
    language_mismatch_fields: tuple[str, ...]
    profile_type: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    not_applicable_fields: tuple[str, ...]
    missing_required_fields: tuple[str, ...]
    completeness: float
    display_mode: str
    profile_available: bool
    publishable: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "available_fields": list(self.available_fields),
            "missing_fields": list(self.missing_fields),
            "insufficient_fields": list(self.insufficient_fields),
            "language_mismatch_fields": list(self.language_mismatch_fields),
            "profile_type": self.profile_type,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "not_applicable_fields": list(self.not_applicable_fields),
            "missing_required_fields": list(self.missing_required_fields),
            "completeness": self.completeness,
            "display_mode": self.display_mode,
            "profile_available": self.profile_available,
            "publishable": self.publishable,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class KnowledgeEvidenceAssessment:
    """Readiness of a source packet before any disease prose is generated."""

    sufficient: bool
    grounded_source_count: int
    authoritative_source_count: int
    scholarly_source_count: int
    content_characters: int
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "grounded_source_count": self.grounded_source_count,
            "authoritative_source_count": self.authoritative_source_count,
            "scholarly_source_count": self.scholarly_source_count,
            "content_characters": self.content_characters,
            "issues": list(self.issues),
        }


def normalize_knowledge_text(value: Any) -> str | None:
    """Return compact user-facing text, treating blanks and JSON-ish values as missing."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text or text.lower() in {"none", "null", "n/a", "na", "unknown", "-", "—"}:
        return None
    return text


def assess_knowledge_field(value: Any, language: str) -> KnowledgeFieldAssessment:
    """Distinguish real content from blank, unsupported, or wrong-language text."""
    language = "zh" if language == "zh" else "en"
    text = normalize_knowledge_text(value)
    if text is None:
        return KnowledgeFieldAssessment(FIELD_STATUS_MISSING, "empty", 0, 0)

    without_citations = _CITATION_RE.sub("", text)
    cjk_count = len(_CJK_RE.findall(without_citations))
    latin_count = len(_LATIN_RE.findall(without_citations))
    meaningful_character_count = cjk_count + latin_count

    minimum_character_count = 8 if language == "zh" else 12
    if meaningful_character_count < minimum_character_count:
        return KnowledgeFieldAssessment(FIELD_STATUS_INSUFFICIENT, "too_short", 1, 0)
    if language == "zh" and cjk_count < 8 and latin_count >= 24:
        return KnowledgeFieldAssessment(FIELD_STATUS_LANGUAGE_MISMATCH, "expected_zh", 1, 0)
    if language == "en" and cjk_count >= 12 and cjk_count > latin_count:
        return KnowledgeFieldAssessment(FIELD_STATUS_LANGUAGE_MISMATCH, "expected_en", 1, 0)

    sentences = [item.strip() for item in _SENTENCE_SPLIT_RE.split(without_citations) if item.strip()]
    if not sentences:
        sentences = [without_citations]
    unavailable_count = sum(1 for sentence in sentences if _sentence_is_unavailable(sentence, language))

    # A limitation sentence may appropriately qualify a real claim. Reject the
    # field only when every sentence is absence text or bibliographic metadata.
    if unavailable_count == len(sentences):
        return KnowledgeFieldAssessment(
            FIELD_STATUS_INSUFFICIENT,
            "evidence_unavailable_placeholder",
            len(sentences),
            unavailable_count,
        )

    return KnowledgeFieldAssessment(FIELD_STATUS_AVAILABLE, None, len(sentences), unavailable_count)


def strip_unavailable_knowledge_sentences(value: Any, language: str) -> str | None:
    """Remove repetitive absence/metadata sentences when they dominate a field.

    A single trailing limitation is retained when most of the paragraph is
    useful. If limitation text is the majority, only genuinely supported
    sentences survive; if none survive, the field becomes missing.
    """
    language = "zh" if language == "zh" else "en"
    text = normalize_knowledge_text(value)
    if text is None:
        return None
    sentences = [item.strip() for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    if not sentences:
        return text
    unavailable = [_sentence_is_unavailable(_CITATION_RE.sub("", sentence), language) for sentence in sentences]
    unavailable_count = sum(unavailable)
    if unavailable_count == len(sentences):
        return None
    if unavailable_count * 2 >= len(sentences):
        return " ".join(sentence for sentence, rejected in zip(sentences, unavailable) if not rejected)
    return text


def assess_knowledge_brief(
    payload: Any,
    language: str | None = None,
    *,
    disease: Any | None = None,
) -> KnowledgeBriefAssessment:
    """Assess one stored/generated brief without mutating it.

    ``partial`` is a first-class public state. It requires a usable lead and at
    least one real profile section. A brief made entirely from absence
    explanations is ``blocked``; surveillance data is not its replacement.
    """
    language = "zh" if (language or _value(payload, "language")) == "zh" else "en"
    profile_schema = (
        resolve_knowledge_profile_schema(disease)
        if disease is not None
        else profile_schema_from_payload(payload)
    )
    fields = {
        field: (
            KnowledgeFieldAssessment(FIELD_STATUS_NOT_APPLICABLE, "profile_schema", 0, 0)
            if field in profile_schema.not_applicable_fields
            else assess_knowledge_field(_raw_value(payload, field), language)
        )
        for field in KNOWLEDGE_TEXT_FIELDS
    }
    available_fields = tuple(field for field, result in fields.items() if result.available)
    missing_fields = tuple(field for field, result in fields.items() if result.status == FIELD_STATUS_MISSING)
    insufficient_fields = tuple(
        field for field, result in fields.items() if result.status == FIELD_STATUS_INSUFFICIENT
    )
    language_mismatch_fields = tuple(
        field for field, result in fields.items() if result.status == FIELD_STATUS_LANGUAGE_MISMATCH
    )
    missing_required_fields = tuple(
        field for field in profile_schema.required_fields if not fields[field].available
    )

    applicable_fields = profile_schema.applicable_fields
    available_sections = sum(1 for field in applicable_fields if fields[field].available)
    has_lead = fields["brief"].available or fields["definition"].available
    profile_available = has_lead and available_sections >= 1
    completeness = round(available_sections / len(applicable_fields), 3) if applicable_fields else 0.0
    if profile_available and not missing_required_fields:
        display_mode = "full"
    elif profile_available:
        display_mode = "partial"
    else:
        display_mode = "blocked"

    has_sources = bool(_raw_value(payload, "source_ids") or _raw_value(payload, "source_attribution"))
    publishable = (
        profile_available
        and has_sources
        and not missing_required_fields
        and not insufficient_fields
        and not language_mismatch_fields
    )
    issues: list[str] = []
    if not has_lead:
        issues.append("missing substantive brief or definition")
    if available_sections < 1:
        issues.append("missing substantive profile sections")
    if missing_required_fields:
        issues.append(f"missing required sections: {', '.join(missing_required_fields)}")
    if not has_sources:
        issues.append("missing traceable sources")
    if language_mismatch_fields:
        issues.append(f"language mismatch: {', '.join(language_mismatch_fields)}")
    if insufficient_fields:
        issues.append(f"insufficient evidence: {', '.join(insufficient_fields)}")

    return KnowledgeBriefAssessment(
        language=language,
        fields=fields,
        available_fields=available_fields,
        missing_fields=missing_fields,
        insufficient_fields=insufficient_fields,
        language_mismatch_fields=language_mismatch_fields,
        profile_type=profile_schema.profile_type,
        required_fields=profile_schema.required_fields,
        optional_fields=profile_schema.optional_fields,
        not_applicable_fields=profile_schema.not_applicable_fields,
        missing_required_fields=missing_required_fields,
        completeness=completeness,
        display_mode=display_mode,
        profile_available=profile_available,
        publishable=publishable,
        issues=tuple(issues),
    )


def sanitize_knowledge_brief(
    payload: dict[str, Any],
    *,
    strip_brief: bool = False,
) -> tuple[dict[str, Any], KnowledgeBriefAssessment]:
    """Return a copy with non-knowledge placeholders removed from public fields."""
    language = "zh" if payload.get("language") == "zh" else "en"
    assessment = assess_knowledge_brief(payload, language)
    cleaned = dict(payload)
    for field, result in assessment.fields.items():
        if field == "brief" and not strip_brief:
            continue
        cleaned[field] = (
            strip_unavailable_knowledge_sentences(payload.get(field), language)
            if result.available
            else None
        )
    cleaned["clinical_summary"] = cleaned.get("clinical_features")
    cleaned["quality"] = assessment.to_dict()
    return cleaned, assessment


def apply_knowledge_quality_gate(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], KnowledgeBriefAssessment]:
    """Sanitize generated fields and prevent false-positive publication.

    The original unavailability prose remains available in task logs/model
    traces. Persisted public fields use ``None`` for unsupported optional
    sections so downstream consumers cannot mistake a disclaimer for content.
    """
    cleaned, assessment = sanitize_knowledge_brief(payload)
    metadata = cleaned.get("metadata") if isinstance(cleaned.get("metadata"), dict) else {}
    cleaned["metadata"] = {
        **metadata,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "quality": assessment.to_dict(),
    }

    current_status = str(cleaned.get("status") or "draft").strip().lower()
    confidence_bonus = {
        "high": 0.12,
        "medium": 0.07,
        "low": 0.02,
    }.get(str(cleaned.get("source_confidence") or "").lower(), 0.0)
    # ``completeness`` includes optional sections for display progress. It is
    # not a publication requirement: surveillance events, classifications, and
    # other narrow schemas may be fully valid with several optional fields
    # intentionally absent. Score required coverage as the decisive signal and
    # let optional coverage contribute only a small quality bonus.
    required_fields = tuple(assessment.required_fields)
    required_coverage = (
        sum(1 for field in required_fields if assessment.fields[field].available)
        / len(required_fields)
        if required_fields
        else 1.0
    )
    optional_fields = tuple(assessment.optional_fields)
    optional_coverage = (
        sum(1 for field in optional_fields if assessment.fields[field].available)
        / len(optional_fields)
        if optional_fields
        else 0.0
    )
    semantic_score = min(
        0.98,
        0.44
        + (required_coverage * 0.40)
        + (optional_coverage * 0.06)
        + confidence_bonus,
    )
    if not assessment.profile_available or not assessment.publishable:
        semantic_score = min(semantic_score, 0.5)
    cleaned["quality_score"] = round(semantic_score, 3)
    if current_status == "published" and (
        not assessment.publishable
        or cleaned["quality_score"] < KNOWLEDGE_PUBLICATION_MIN_QUALITY_SCORE
    ):
        # Missing evidence is a machine-actionable workflow state, not a
        # request for a person to approve unsupported health content.
        cleaned["status"] = "draft"
        cleaned["metadata"] = {
            **cleaned["metadata"],
            "automation_state": "awaiting_evidence",
            "block_reason": "missing_required_sections",
        }

    cleaned["review_notes"] = _replace_quality_review_notes(
        cleaned.get("review_notes"),
        assessment,
    )
    return cleaned, assessment


def _replace_quality_review_notes(
    existing_notes: Any,
    assessment: KnowledgeBriefAssessment,
) -> str:
    retained_notes = _strip_existing_quality_review_notes(existing_notes)
    quality_note = _quality_review_note_for(assessment)
    if retained_notes:
        return f"{retained_notes}; {quality_note}"
    return quality_note


def _quality_review_note_for(assessment: KnowledgeBriefAssessment) -> str:
    if not assessment.profile_available:
        if assessment.issues:
            return f"{QUALITY_REVIEW_NOTE_BLOCKED_PREFIX}; {'; '.join(assessment.issues)}"
        return QUALITY_REVIEW_NOTE_BLOCKED_PREFIX
    if assessment.issues:
        return f"{QUALITY_REVIEW_NOTE_PARTIAL_PREFIX}; {'; '.join(assessment.issues)}"
    return QUALITY_REVIEW_NOTE_READY


def _strip_existing_quality_review_notes(existing_notes: Any) -> str | None:
    text = normalize_knowledge_text(existing_notes)
    if text is None:
        return None
    for note in (
        QUALITY_REVIEW_NOTE_READY,
        QUALITY_REVIEW_NOTE_PARTIAL_PREFIX,
        QUALITY_REVIEW_NOTE_BLOCKED_PREFIX,
    ):
        text = text.replace(note, "")
    segments = [segment.strip() for segment in text.split(";") if segment.strip()]
    retained: list[str] = []
    for segment in segments:
        if _is_quality_issue_segment(segment):
            continue
        retained.append(segment)
    return "; ".join(retained) if retained else None


def _is_quality_issue_segment(segment: str) -> bool:
    return (
        segment.startswith("missing required sections:")
        or segment.startswith("missing substantive")
        or segment.startswith("missing traceable sources")
        or segment.startswith("language mismatch:")
        or segment.startswith("insufficient evidence:")
    )


def has_grounding_content(source: Any, *, minimum_characters: int = 80) -> bool:
    """Return whether a source contains evidence beyond a title/metadata match."""
    metadata = _raw_value(source, "metadata")
    if not isinstance(metadata, dict):
        metadata = _raw_value(source, "metadata_")
    metadata = metadata if isinstance(metadata, dict) else {}
    content_kind = str(metadata.get("content_kind") or "").strip().lower()
    if metadata.get("metadata_only") or content_kind in {"scholarly_metadata", "search_result"}:
        return False

    source_type = str(_raw_value(source, "source_type") or "").strip().lower()
    content_text = normalize_knowledge_text(_raw_value(source, "content_text"))
    if source_type == "pubmed":
        if not content_text or content_text.lower().startswith("review article:"):
            return False
        return len(content_text) >= minimum_characters

    return len(_unique_grounding_text(source)) >= minimum_characters


def assess_knowledge_evidence(sources: Iterable[Any]) -> KnowledgeEvidenceAssessment:
    """Require a diverse, substantive evidence packet before generation starts.

    One rich authority page can be sufficient. Otherwise at least two grounded
    sources are required, including an authority page or a scholarly abstract.
    Title metadata, search-result snippets, rejected rows, and stale rows never
    contribute to readiness.
    """
    grounded_count = 0
    authoritative_count = 0
    scholarly_count = 0
    content_characters = 0

    authority_url_markers = (
        "who.int/",
        "cdc.gov/",
        "nih.gov/",
        "ncbi.nlm.nih.gov/",
        "ecdc.europa.eu/",
        "chp.gov.hk/",
        "health.gov.au/",
        "canada.ca/",
        "gov.uk/",
    )
    eligible_sources: list[tuple[int, Any, str, str]] = []
    for source in sources:
        if str(_raw_value(source, "status") or "active").strip().lower() != "active":
            continue
        if str(_raw_value(source, "review_status") or "pending").strip().lower() != "approved":
            continue
        metadata = _raw_value(source, "metadata")
        if not isinstance(metadata, dict):
            metadata = _raw_value(source, "metadata_")
        metadata = metadata if isinstance(metadata, dict) else {}
        relevance_score = metadata.get("relevance_score")
        try:
            if relevance_score is not None and float(relevance_score) < 0.5:
                continue
        except (TypeError, ValueError):
            continue
        if not has_grounding_content(source):
            continue

        grounding_text = _unique_grounding_text(source)
        eligible_sources.append(
            (
                len(grounding_text),
                source,
                _canonical_source_url(source),
                hashlib.sha256(grounding_text.encode("utf-8")).hexdigest(),
            )
        )

    # One page may be discovered through multiple adapters and each parsed page
    # is stored as overlapping excerpt/text/section views. Count the underlying
    # document once so duplicated storage cannot manufacture evidence volume or
    # source diversity.
    seen_urls: set[str] = set()
    seen_content_hashes: set[str] = set()
    for content_length, source, canonical_url, content_hash in sorted(
        eligible_sources, key=lambda item: item[0], reverse=True
    ):
        if canonical_url and canonical_url in seen_urls:
            continue
        if content_hash in seen_content_hashes:
            continue
        if canonical_url:
            seen_urls.add(canonical_url)
        seen_content_hashes.add(content_hash)

        grounded_count += 1
        metadata = _raw_value(source, "metadata")
        if not isinstance(metadata, dict):
            metadata = _raw_value(source, "metadata_")
        metadata = metadata if isinstance(metadata, dict) else {}
        source_type = str(_raw_value(source, "source_type") or "").strip().lower()
        url = str(
            _raw_value(source, "resolved_url")
            or _raw_value(source, "url")
            or ""
        ).lower()
        is_authoritative = (
            source_type in {"who", "who_don"}
            or str(metadata.get("authority_level") or "").lower() == "high"
            or (
                source_type != "pubmed"
                and any(marker in url for marker in authority_url_markers)
            )
        )
        if is_authoritative:
            authoritative_count += 1
        if source_type == "pubmed" or str(metadata.get("content_kind") or "").lower() == "abstract":
            scholarly_count += 1

        content_characters += content_length

    has_evidence_class = authoritative_count > 0 or scholarly_count > 0
    sufficient = (
        authoritative_count >= 1 and content_characters >= 900
    ) or (
        grounded_count >= 2 and has_evidence_class and content_characters >= 600
    )
    issues: list[str] = []
    if grounded_count == 0:
        issues.append("no_grounded_sources")
    elif grounded_count < 2 and authoritative_count == 0:
        issues.append("insufficient_source_diversity")
    if not has_evidence_class:
        issues.append("no_authoritative_or_scholarly_source")
    if content_characters < 600:
        issues.append("insufficient_source_content")

    return KnowledgeEvidenceAssessment(
        sufficient=sufficient,
        grounded_source_count=grounded_count,
        authoritative_source_count=authoritative_count,
        scholarly_source_count=scholarly_count,
        content_characters=content_characters,
        issues=tuple(issues),
    )


def _grounding_content_length(source: Any) -> int:
    return len(_unique_grounding_text(source))


def _grounding_text_parts(source: Any) -> list[str]:
    parts: list[str] = []
    for key in ("content_text", "raw_excerpt", "snippet", "description"):
        value = normalize_knowledge_text(_raw_value(source, key))
        if value:
            parts.append(value)
    sections = _raw_value(source, "content_sections")
    if isinstance(sections, Iterable) and not isinstance(sections, (str, bytes, dict)):
        for section in sections:
            if not isinstance(section, dict):
                continue
            value = normalize_knowledge_text(
                section.get("text") or section.get("content") or section.get("body") or section.get("summary")
            )
            if value:
                parts.append(value)
    return parts


def _unique_grounding_text(source: Any) -> str:
    """Collapse overlapping source representations into one evidence string."""
    unique_parts: list[str] = []
    seen: set[str] = set()
    # Longest-first makes excerpts and section copies disappear when they are
    # already contained in the parsed page body.
    for part in sorted(_grounding_text_parts(source), key=len, reverse=True):
        key = part.casefold()
        if key in seen or any(key in existing for existing in seen):
            continue
        seen.add(key)
        unique_parts.append(part)
    return " ".join(unique_parts)


def _canonical_source_url(source: Any) -> str:
    metadata = _raw_value(source, "metadata")
    if not isinstance(metadata, dict):
        metadata = _raw_value(source, "metadata_")
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = str(
        metadata.get("canonical_url")
        or _raw_value(source, "resolved_url")
        or _raw_value(source, "url")
        or ""
    ).strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.casefold()
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(filtered_query),
            "",
        )
    )


def _raw_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _sentence_is_unavailable(sentence: str, language: str) -> bool:
    return is_unavailable_knowledge_sentence(sentence, language)


def is_unavailable_knowledge_sentence(sentence: str, language: str) -> bool:
    """Return whether a sentence says the requested evidence is unavailable.

    Evidence selection uses the same semantic boundary as profile publication.
    This prevents a source disclaimer from being mistaken for support merely
    because it repeats a section keyword such as ``incidence`` or ``burden``.
    """
    unavailable_pattern = _UNAVAILABLE_ZH_RE if language == "zh" else _UNAVAILABLE_EN_RE
    metadata_pattern = _METADATA_ONLY_ZH_RE if language == "zh" else _METADATA_ONLY_EN_RE
    return bool(unavailable_pattern.search(sentence) or metadata_pattern.search(sentence))


def _value(obj: Any, key: str) -> str:
    value = _raw_value(obj, key)
    return str(value).strip() if value not in (None, "") else ""
