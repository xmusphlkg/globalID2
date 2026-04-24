"""Knowledge-base ingestion and source-grounded brief generation."""

from .brief_generator import SourceGroundedBriefGenerator
from .catalogue import (
    build_catalogue_disease_brief,
    build_catalogue_disease_brief_payload,
    knowledge_brief_fallback_reason,
    knowledge_brief_publication_tier,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from .llm_brief_generator import AIDiseaseBriefGenerator
from .sources import DiseaseKnowledgeFetcher, SourceCandidate

__all__ = [
    "AIDiseaseBriefGenerator",
    "DiseaseKnowledgeFetcher",
    "SourceCandidate",
    "SourceGroundedBriefGenerator",
    "build_catalogue_disease_brief",
    "build_catalogue_disease_brief_payload",
    "knowledge_brief_fallback_reason",
    "knowledge_brief_publication_tier",
    "public_disease_page_exclusion_reason",
    "resolve_disease_knowledge_status",
    "should_generate_public_disease_page",
]
