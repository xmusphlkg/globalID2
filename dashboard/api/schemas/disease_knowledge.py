"""Request and response contracts for disease knowledge."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .task import TaskOut


class DiseaseKnowledgeCatalogueItem(BaseModel):
    disease_id: str
    name_en: Optional[str] = None
    name_zh: Optional[str] = None
    category: Optional[str] = None
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    knowledge_status: str = "blocked"
    knowledge_updated_at: Optional[str] = None
    published_languages: List[str] = Field(default_factory=list)
    blocked_languages: List[str] = Field(default_factory=list)
    knowledge_display_mode: str = "blocked"
    knowledge_completeness: float = 0.0
    knowledge_profile_type: str = "infectious_disease"
    knowledge_profile_schema: Dict[str, Any] = Field(default_factory=dict)
    repair_sections: List[str] = Field(default_factory=list)
    repair_sections_by_language: Dict[str, List[str]] = Field(default_factory=dict)
    repair_reasons_by_language: Dict[str, List[str]] = Field(default_factory=dict)
    required_gap_sections: List[str] = Field(default_factory=list)
    repair_priority: str = "none"
    language_quality: Dict[str, Any] = Field(default_factory=dict)
    source_count: int = 0
    brief_statuses: Dict[str, str] = Field(default_factory=dict)
    brief_tiers: Dict[str, str] = Field(default_factory=dict)

class DiseaseKnowledgeTaskSkipped(BaseModel):
    disease_id: str
    reason: str
    existing_task_uuid: Optional[str] = None
    existing_status: Optional[str] = None

class DiseaseKnowledgeStartRequest(BaseModel):
    disease_ids: List[str] = Field(..., min_length=1)
    source: List[str] = Field(default_factory=list, description="Source groups: who / search / wikidata / wikipedia / pubmed / msd")
    force: bool = False
    generator: str = Field("auto", description="auto prefers reviewed profiles; ai always uses the model center")
    priority: str = Field("normal")
    task_name: Optional[str] = Field(None, description="Optional batch task name prefix")
    description: Optional[str] = Field(None, description="Optional task description")

class DiseaseKnowledgeStartResponse(BaseModel):
    requested_disease_ids: List[str]
    created_tasks: List[TaskOut]
    skipped: List[DiseaseKnowledgeTaskSkipped] = Field(default_factory=list)

class DiseaseKnowledgeRepairRequest(BaseModel):
    source: List[str] = Field(default_factory=list, description="Source groups: who / search / wikidata / wikipedia / pubmed / msd")
    force: bool = False
    generator: str = Field("ai", description="ai uses the model center for targeted repair")
    priority: Optional[str] = Field(None, description="Optional override: urgent / high / normal / low")
    limit: Optional[int] = Field(None, ge=1, le=500)

class DiseaseKnowledgeRepairResponse(BaseModel):
    created_count: int
    skipped_count: int
    source_groups: List[str]
    generator: str
    force: bool
    created_tasks: List[TaskOut]
    skipped: List[DiseaseKnowledgeTaskSkipped] = Field(default_factory=list)

class DiseaseKnowledgeSourceDetail(BaseModel):
    id: int
    disease_id: str
    source_type: str
    source_name: str
    url: str
    resolved_url: Optional[str] = None
    title: Optional[str] = None
    license: Optional[str] = None
    status: str
    language: str
    raw_excerpt: Optional[str] = None
    content_text: Optional[str] = None
    content_sections: List[Dict[str, Any]] = Field(default_factory=list)
    raw_excerpt_hash: Optional[str] = None
    fetched_at: Optional[str] = None
    review_status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DiseaseKnowledgeBriefDetail(BaseModel):
    language: str
    status: str
    source_confidence: str
    updated_at: Optional[str] = None
    brief: str
    definition: Optional[str] = None
    clinical_features: Optional[str] = None
    clinical_summary: Optional[str] = None
    epidemiology: Optional[str] = None
    transmission: Optional[str] = None
    prevention: Optional[str] = None
    surveillance_note: Optional[str] = None
    risk_groups: Optional[str] = None
    disclaimer: Optional[str] = None
    model: Optional[str] = None
    quality_score: Optional[float] = None
    review_notes: Optional[str] = None
    source_ids: List[int] = Field(default_factory=list)
    source_attribution: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    quality: Dict[str, Any] = Field(default_factory=dict)

class DiseaseKnowledgeDetail(BaseModel):
    disease_id: str
    name_en: Optional[str] = None
    name_zh: Optional[str] = None
    category: Optional[str] = None
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    knowledge_status: str = "blocked"
    knowledge_updated_at: Optional[str] = None
    published_languages: List[str] = Field(default_factory=list)
    blocked_languages: List[str] = Field(default_factory=list)
    knowledge_display_mode: str = "blocked"
    knowledge_completeness: float = 0.0
    knowledge_profile_type: str = "infectious_disease"
    knowledge_profile_schema: Dict[str, Any] = Field(default_factory=dict)
    repair_sections: List[str] = Field(default_factory=list)
    repair_sections_by_language: Dict[str, List[str]] = Field(default_factory=dict)
    repair_reasons_by_language: Dict[str, List[str]] = Field(default_factory=dict)
    required_gap_sections: List[str] = Field(default_factory=list)
    repair_priority: str = "none"
    language_quality: Dict[str, Any] = Field(default_factory=dict)
    evidence_quality: Dict[str, Any] = Field(default_factory=dict)
    source_count: int = 0
    brief_statuses: Dict[str, str] = Field(default_factory=dict)
    brief_tiers: Dict[str, str] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    briefs: List[DiseaseKnowledgeBriefDetail] = Field(default_factory=list)
    sources: List[DiseaseKnowledgeSourceDetail] = Field(default_factory=list)
