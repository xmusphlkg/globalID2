"""Knowledge-base ingestion and source-grounded brief generation."""

from .evidence import EvidenceFragment, EvidenceManifest, build_evidence_manifest
from .catalogue import (
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from .llm_brief_generator import AIDiseaseBriefGenerator
from .profile_schema import (
    KnowledgeProfileSchema,
    attach_profile_schema,
    profile_schema_from_payload,
    resolve_knowledge_profile_schema,
)
from .quality import (
    KNOWLEDGE_TEXT_FIELDS,
    PROFILE_SECTION_FIELDS,
    apply_knowledge_quality_gate,
    assess_knowledge_evidence,
    assess_knowledge_brief,
    assess_knowledge_field,
    has_grounding_content,
    sanitize_knowledge_brief,
    strip_unavailable_knowledge_sentences,
)
from .sources import DiseaseKnowledgeFetcher, SourceCandidate

__all__ = [
    "AIDiseaseBriefGenerator",
    "DiseaseKnowledgeFetcher",
    "EvidenceFragment",
    "EvidenceManifest",
    "SourceCandidate",
    "KNOWLEDGE_TEXT_FIELDS",
    "KnowledgeProfileSchema",
    "PROFILE_SECTION_FIELDS",
    "apply_knowledge_quality_gate",
    "attach_profile_schema",
    "assess_knowledge_evidence",
    "assess_knowledge_brief",
    "assess_knowledge_field",
    "build_evidence_manifest",
    "knowledge_brief_block_reason",
    "knowledge_brief_publication_tier",
    "public_disease_page_exclusion_reason",
    "resolve_disease_knowledge_status",
    "profile_schema_from_payload",
    "resolve_knowledge_profile_schema",
    "has_grounding_content",
    "sanitize_knowledge_brief",
    "strip_unavailable_knowledge_sentences",
    "should_generate_public_disease_page",
]
