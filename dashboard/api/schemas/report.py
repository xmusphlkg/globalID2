"""Report schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ReportOut(BaseModel):
    id: int
    report_uuid: str
    title: str
    report_type: str
    status: str
    country_id: int
    country_name: Optional[str] = None
    period_start: datetime
    period_end: datetime
    quality_score: Optional[float] = None
    generation_time: Optional[float] = None
    section_count: int = 0
    primary_disease: Optional[str] = None
    disease_names: List[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportDetailOut(BaseModel):
    id: int
    report_uuid: str
    title: str
    report_type: str
    status: str
    country_id: int
    country_name: Optional[str] = None
    period_start: datetime
    period_end: datetime
    summary: Optional[str] = None
    key_findings: list = []
    recommendations: list = []
    quality_score: Optional[float] = None
    generation_time: Optional[float] = None
    token_usage: Optional[Dict[str, Any]] = None
    ai_model_used: Optional[str] = None
    html_path: Optional[str] = None
    pdf_path: Optional[str] = None
    markdown_path: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    analysis_summary: Optional[Dict[str, Any]] = None
    quality_gate: Optional[Dict[str, Any]] = None
    data_quality: Optional[Dict[str, Any]] = None
    method_version: Optional[str] = None
    created_at: datetime
    sections: List["ReportSectionOut"] = []

    model_config = {"from_attributes": True}


class ReportSectionOut(BaseModel):
    id: int
    section_type: Optional[str] = None
    section_order: int = 0
    title: Optional[str] = None
    content: Optional[str] = None
    ai_model: Optional[str] = None
    generation_time: Optional[float] = None
    data_sources: Optional[Any] = None
    charts: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportSectionRunOut(BaseModel):
    id: int
    run_uuid: Optional[str] = None
    section_id: Optional[int] = None
    disease_name: Optional[str] = None
    section_type: Optional[str] = None
    status: str
    model: Optional[str] = None
    provider: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    token_usage: Optional[Dict[str, Any]] = None
    quality_scores: Optional[Dict[str, Any]] = None
    revision_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AIConversationOut(BaseModel):
    id: int
    agent: Optional[str] = None
    timestamp: Optional[datetime] = None
    prompt: Optional[str] = None
    response: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


class AIInteractionOut(BaseModel):
    id: int
    task_uuid: Optional[str] = None
    task_name: Optional[str] = None
    task_status: Optional[str] = None
    report_id: int
    report_uuid: str
    report_status: Optional[str] = None
    report_title: str
    country_id: int
    section_id: Optional[int] = None
    section_type: Optional[str] = None
    section_title: Optional[str] = None
    disease_name: Optional[str] = None
    run_id: int
    run_uuid: Optional[str] = None
    run_status: Optional[str] = None
    run_model: Optional[str] = None
    run_provider: Optional[str] = None
    run_temperature: Optional[float] = None
    agent: Optional[str] = None
    role: Optional[str] = None
    timestamp: Optional[datetime] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens: Optional[Dict[str, Any]] = None
    total_tokens: int = 0
    duration: Optional[float] = None
    quality_scores: Optional[Dict[str, Any]] = None
    quality_overall: Optional[float] = None
    system_prompt: Optional[str] = None
    prompt: Optional[str] = None
    response: Optional[str] = None
    temperature: Optional[float] = None


class AIInteractionSummaryOut(BaseModel):
    total_interactions: int
    total_tokens: int
    avg_tokens: float
    avg_duration: float
    avg_quality: Optional[float] = None
    by_agent: Dict[str, int]
    by_model: Dict[str, int]
    task_uuid: Optional[str] = None
