"""Request and response contracts for ai tasks."""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class AIStartRequest(BaseModel):
    """Body for POST /reports/runs."""

    country_code: str = Field(..., min_length=2, max_length=10, description="Stable country code")
    report_type: str = Field("monthly", description="Report type: daily / weekly / monthly / special")
    period_start: Optional[str] = Field(None, description="ISO datetime, optional")
    period_end: Optional[str] = Field(None, description="ISO datetime, optional")
    language: str = Field("en", description="Report language: zh or en")
    days: int = Field(365, ge=1, le=3650, description="Fallback period in days")
    enable_review: bool = Field(True, description="Enable reviewer agent")
    report_layout: str = Field(
        "analytical_v3",
        description="Report layout: analytical_v3 | structured | legacy",
    )
    analysis_depth: str = Field(
        "deep",
        description="Analytical v3 depth: deep | deterministic",
    )
    quality_threshold: float = Field(
        0.85,
        ge=0.0,
        le=1.0,
        description="Minimum quality gate score for automatic approval",
    )
    send_email: bool = Field(
        False,
        description="Send the completed report email to the centralized Settings recipients after generation",
    )
    reuse_from_failed: bool = Field(True, description="Reuse partial output from failed/generating tasks in same scope")
    reuse_strategy: str = Field(
        "auto",
        description="Reuse strategy: auto | safe | resume | manual",
    )
    reuse_report_uuid: Optional[UUID] = Field(
        None,
        description="Optional explicit report UUID when reuse_strategy=manual",
    )
    priority: str = Field("normal", description="Task priority")
    task_name: Optional[str] = Field(None, description="Optional custom task name")
    description: Optional[str] = Field(None, description="Optional task description")

class AIContentGovernanceRequest(BaseModel):
    dry_run: bool = False
    knowledge_retry_limit: int = Field(50, ge=0, le=500)
    literature_limit: int = Field(20, ge=0, le=100)
    literature_article_limit: Optional[int] = Field(None, ge=0, le=100)
    source_limit: int = Field(30, ge=0, le=200)
    learning_limit: int = Field(20, ge=0, le=100)

class AIContentGovernanceResponse(BaseModel):
    prompt_version: str
    dry_run: bool
    knowledge: Dict[str, Any]
    literature_articles: Dict[str, Any]
    literature: Dict[str, Any]
    knowledge_sources: Dict[str, Any]
    learning_suggestions: Dict[str, Any]

class KnowledgeRepairRetryRequest(BaseModel):
    limit: int = Field(50, ge=1, le=500)

class DiseaseDuplicateAuditRequest(BaseModel):
    include_ai: bool = Field(True, description="Ask the model center to classify findings")
    include_new_disease_candidates: bool = Field(
        True,
        description="Scan current source data for unmapped disease terms that may need new standard diseases",
    )
    max_ai_candidates: int = Field(40, ge=1, le=100)

class DiseaseDuplicateAuditStatusRequest(BaseModel):
    include_new_disease_candidates: bool = True
