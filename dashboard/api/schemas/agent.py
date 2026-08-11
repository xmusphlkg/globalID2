"""Agent workflow schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .task import TaskDetailOut, TaskOut


DEFAULT_AGENT_ACTIONS = [
    "crawl_data",
    "generate_report",
    "update_disease_knowledge",
    "export_data",
]


class AgentWorkflowCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    country_code: Optional[str] = Field(None, min_length=2, max_length=10)
    mode: str = Field("research")
    output_format: str = Field("evidence_report")
    allowed_actions: List[str] = Field(default_factory=lambda: list(DEFAULT_AGENT_ACTIONS))
    memory_scope: str = Field("project")
    search_scope: str = Field("web+db+memory")
    priority: str = Field("normal")
    task_name: Optional[str] = None
    description: Optional[str] = None


class AgentWorkflowRunOut(BaseModel):
    id: int
    task_id: int
    mode: str
    output_format: str
    prompt: str
    status: str
    risk_level: str
    country_id: Optional[int] = None
    search_scope: str
    memory_scope: str
    allowed_actions: List[str] = Field(default_factory=list)
    plan_json: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    actions_taken: List[Dict[str, Any]] = Field(default_factory=list)
    result_json: Dict[str, Any] = Field(default_factory=dict)
    budget_tokens_total: Optional[int] = None
    budget_tokens_used: int = 0
    replan_count: int = 0
    search_round_count: int = 0
    review_round_count: int = 0
    step_count: int = 0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class AgentWorkflowRunSummaryOut(BaseModel):
    task: TaskOut
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    run: AgentWorkflowRunOut


class AgentWorkflowRunListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[AgentWorkflowRunSummaryOut] = Field(default_factory=list)


class AgentWorkflowStepOut(BaseModel):
    id: int
    step_uuid: str
    run_id: int
    step_key: str
    step_order: int
    step_type: str
    step_name: str
    status: str
    attempt: int
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    response: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens: Dict[str, Any] = Field(default_factory=dict)
    duration: Optional[float] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class AgentWorkflowEvidenceOut(BaseModel):
    id: int
    evidence_uuid: str
    run_id: int
    step_id: Optional[int] = None
    evidence_type: str
    source_type: str
    source_name: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    resolved_url: Optional[str] = None
    content_snippet: Optional[str] = None
    content_hash: str
    confidence: float
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentWorkflowConversationOut(BaseModel):
    id: int
    conversation_uuid: str
    run_id: int
    step_id: Optional[int] = None
    agent_role: str
    phase: str
    timestamp: Optional[datetime] = None
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    response: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens: Dict[str, Any] = Field(default_factory=dict)
    duration: Optional[float] = None
    temperature: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentWorkflowMemoryOut(BaseModel):
    id: int
    memory_uuid: str
    run_id: Optional[int] = None
    task_id: Optional[int] = None
    scope: str
    memory_type: str
    content: Optional[str] = None
    summary: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    content_hash: str
    embedding: List[float] = Field(default_factory=list)
    collection_name: Optional[str] = None
    qdrant_point_id: Optional[str] = None
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentWorkflowRunDetailOut(BaseModel):
    task: TaskDetailOut
    run: AgentWorkflowRunOut
    steps: List[AgentWorkflowStepOut] = Field(default_factory=list)
    evidence: List[AgentWorkflowEvidenceOut] = Field(default_factory=list)
    conversations: List[AgentWorkflowConversationOut] = Field(default_factory=list)
    memories: List[AgentWorkflowMemoryOut] = Field(default_factory=list)


class AgentWorkflowActionOut(BaseModel):
    task_uuid: str
    task_status: str
    run_status: Optional[str] = None
    cancel_requested: bool = False
    message: Optional[str] = None
    detail: Optional[AgentWorkflowRunDetailOut] = None
