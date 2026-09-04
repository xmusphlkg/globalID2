"""Knowledge-base ingestion and source-grounded brief generation."""

from .evidence import (
    EvidenceFragment,
    EvidenceManifest,
    EvidencePacket,
    KnowledgeSectionCoverage,
    MAX_EVIDENCE_MANIFEST_CHARACTERS,
    assess_evidence_section_coverage,
    build_evidence_manifest,
    prepare_evidence_packet,
)
from .catalogue import (
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from .llm_brief_generator import AIDiseaseBriefGenerator
from .reviewed_brief_generator import ReviewedDiseaseBriefGenerator
from .profile_schema import (
    KnowledgeProfileSchema,
    attach_profile_schema,
    profile_schema_from_payload,
    resolve_knowledge_profile_schema,
)
from .quality import (
    KNOWLEDGE_TEXT_FIELDS,
    KNOWLEDGE_PUBLICATION_MIN_QUALITY_SCORE,
    KNOWLEDGE_SCHEMA_VERSION,
    EVIDENCE_POLICY_VERSION,
    PROFILE_SECTION_FIELDS,
    apply_knowledge_quality_gate,
    assess_knowledge_evidence,
    assess_knowledge_brief,
    assess_knowledge_field,
    has_grounding_content,
    is_unavailable_knowledge_sentence,
    sanitize_knowledge_brief,
    strip_unavailable_knowledge_sentences,
)
from .sources import (
    DiseaseKnowledgeFetcher,
    KNOWLEDGE_SOURCE_STRATEGY_VERSION,
    SourceCandidate,
    SourceFetchReport,
)

__all__ = [
    "AIDiseaseBriefGenerator",
    "ReviewedDiseaseBriefGenerator",
    "DiseaseKnowledgeFetcher",
    "EvidenceFragment",
    "EvidenceManifest",
    "EvidencePacket",
    "KnowledgeSectionCoverage",
    "MAX_EVIDENCE_MANIFEST_CHARACTERS",
    "KNOWLEDGE_SOURCE_STRATEGY_VERSION",
    "SourceCandidate",
    "SourceFetchReport",
    "KNOWLEDGE_TEXT_FIELDS",
    "KNOWLEDGE_PUBLICATION_MIN_QUALITY_SCORE",
    "KNOWLEDGE_SCHEMA_VERSION",
    "EVIDENCE_POLICY_VERSION",
    "KnowledgeProfileSchema",
    "PROFILE_SECTION_FIELDS",
    "apply_knowledge_quality_gate",
    "attach_profile_schema",
    "assess_knowledge_evidence",
    "assess_evidence_section_coverage",
    "assess_knowledge_brief",
    "assess_knowledge_field",
    "build_evidence_manifest",
    "prepare_evidence_packet",
    "knowledge_brief_block_reason",
    "knowledge_brief_publication_tier",
    "public_disease_page_exclusion_reason",
    "resolve_disease_knowledge_status",
    "profile_schema_from_payload",
    "resolve_knowledge_profile_schema",
    "has_grounding_content",
    "is_unavailable_knowledge_sentence",
    "sanitize_knowledge_brief",
    "strip_unavailable_knowledge_sentences",
    "should_generate_public_disease_page",
]
