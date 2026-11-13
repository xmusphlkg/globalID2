"""Publication discovery contracts; preview plans do not authorize execution."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PublicationChannelOut(BaseModel):
    id: str
    label: str
    enabled: bool
    automatic: bool
    realtime_controls: bool = False
    rollback: bool = False
    supported_modes: list[str]
    reason: str | None = None


class PublicationModeOut(BaseModel):
    id: str
    supported: bool
    reason: str | None = None


class PublicationCapabilitiesOut(BaseModel):
    schema_version: Literal[1] = 1
    channels: list[PublicationChannelOut]
    modes: list[PublicationModeOut]
    plan_preview: bool = True
    plan_execution: bool = False
    rollback: bool = False
    legacy_modes: list[str] = Field(default_factory=lambda: ["full", "reports_only", "code_only"])


class PublicationPlanCreate(BaseModel):
    model_config = {"extra": "forbid"}
    job_id: str = Field(default="site-release", min_length=1, max_length=128)
    kind: Literal["auto", "theme", "content", "full", "template"] = "auto"
    channel: Literal["surveillance", "situation", "literature"] | None = None


class PublicationRollbackPlanCreate(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["theme"] = "theme"
    target_plan_id: str
    job_id: str = "site-release"


class PublicationChangedFileOut(BaseModel):
    path: str
    kind: str
    reason: str


class PublicationPlanOut(BaseModel):
    plan_id: str
    plan_digest: str
    job_id: str
    created_at: datetime
    expires_at: datetime
    kind: Literal["content", "theme", "template", "routing", "full"]
    channels: list[str]
    source_revision: str | None
    input_digest: str
    expected_generations: dict[str, str | None]
    affected_templates: list[str]
    content_record_count: int | None
    html_page_count: int | None
    asset_compile_scope: list[str]
    pages_deploy_required: bool
    worker_deploy_required: bool
    validation_scope: list[str]
    cost_estimate_confidence: Literal["unknown", "low", "high"]
    changed_files: list[PublicationChangedFileOut]
    dirty_ids: list[int]
    pending_scopes: dict[str, list[str]]
    warnings: list[str]
    preview_only: bool = True
    executable: bool = False
    execution_mode: str | None = None
    blocked_reason: str | None = None
    rollback_of: str | None = None


class PublicationServiceUsageOut(BaseModel):
    id: str
    requests: int | None = Field(default=None, ge=0)


class PublicationUsageOut(BaseModel):
    plan: Literal["unknown", "free", "paid"] = "unknown"
    status: Literal["unknown", "known", "stale"] = "unknown"
    period_start: datetime | None = None
    period_end: datetime | None = None
    observed_at: datetime | None = None
    source: str | None = None
    account_total: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0)
    remaining: int | None = Field(default=None, ge=0)
    projected_usage: float | None = Field(default=None, ge=0)
    services: list[PublicationServiceUsageOut] = Field(default_factory=list)
    reason: str | None = None
