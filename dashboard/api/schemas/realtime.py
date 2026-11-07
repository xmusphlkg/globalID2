"""Typed controls for the independent Surveillance and Situation JSON channels."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .control_plane import ProblemDetail

RealtimeChannel = Literal["surveillance", "situation"]
RealtimeTaskStatus = Literal["pending", "queued", "running", "retrying", "completed", "failed", "cancelled", "unknown"]


class RealtimeBacklogOut(BaseModel):
    count: int = 0
    scope_count: int = 0
    oldest_at: str | None = None
    oldest_age_seconds: float | None = None
    expanded_country_count: int | None = None
    scopes: list[str] = Field(default_factory=list)


class RealtimeBacklogGroupsOut(BaseModel):
    total: RealtimeBacklogOut
    publishable: RealtimeBacklogOut
    blocked: RealtimeBacklogOut


class RealtimeQueueOut(BaseModel):
    pending: int = 0
    queued: int = 0
    running: int = 0
    retrying: int = 0


class RealtimeRetryOut(BaseModel):
    status: Literal["none", "not_scheduled", "scheduled", "queued", "terminal", "succeeded", "unknown"] = "none"
    scheduled_attempts: int = 0
    max_attempts: int = 0
    next_attempt_at: str | None = None
    retryable: bool = False
    exhausted: bool = False
    category: Literal["permanent", "transient_external", "preflight", "non_external_stage", "unclassified_external", "unknown"] | None = None


class RealtimeContinuationOut(BaseModel):
    status: Literal["none", "pending", "queued", "exhausted", "unknown"] = "none"
    remaining_count: int = 0
    task_uuid: str | None = None


class RealtimeTimingOut(BaseModel):
    export_seconds: float | None = None
    publish_and_ack_seconds: float | None = None
    elapsed_seconds: float | None = None


class RealtimeRunOut(BaseModel):
    task_uuid: str
    status: RealtimeTaskStatus
    progress: int = 0
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    job_id: str | None = None
    release_id: str | None = None
    manifest_sha256: str | None = None
    country_count: int = 0
    selected_change_count: int = 0
    retry: RealtimeRetryOut = Field(default_factory=RealtimeRetryOut)
    continuation: RealtimeContinuationOut = Field(default_factory=RealtimeContinuationOut)
    release_timing: RealtimeTimingOut | None = None
    href: str


class RealtimeMonitorIssueOut(BaseModel):
    level: Literal["warning", "critical"]
    code: str


class RealtimeMonitorOut(BaseModel):
    checked_at: str | None = None
    age_seconds: float | None = None
    stale: bool = True
    status: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    http_status: int | None = None
    sample_verified: bool | None = None
    issues: list[RealtimeMonitorIssueOut] = Field(default_factory=list)


class RealtimeManifestOut(BaseModel):
    release_id: str | None = None
    sha256: str | None = None
    generated_at: str | None = None
    observed_at: str | None = None
    available: bool | None = None
    stale: bool = True


class RealtimeChannelOverviewOut(BaseModel):
    channel: RealtimeChannel
    enabled: bool
    paused: bool
    effective_enabled: bool
    manual_enabled: bool
    control_updated_at: str | None = None
    control_error_code: str | None = None
    backlog: RealtimeBacklogGroupsOut
    queue: RealtimeQueueOut
    current_run: RealtimeRunOut | None = None
    latest_success: RealtimeRunOut | None = None
    latest_failure: RealtimeRunOut | None = None
    failed_last_24h: int = 0
    manifest: RealtimeManifestOut
    monitor: RealtimeMonitorOut


class RealtimeIndependenceOut(BaseModel):
    static_site_separate: Literal[True] = True
    message: str = "Realtime actions publish only JSON. Static HTML has its own release workflow. Pausing automatic publication keeps existing tasks and manual actions available."


class RealtimeOverviewOut(BaseModel):
    checked_at: str
    channels: dict[RealtimeChannel, RealtimeChannelOverviewOut]
    independence: RealtimeIndependenceOut = Field(default_factory=RealtimeIndependenceOut)


class RealtimePendingOut(BaseModel):
    id: int
    channel: RealtimeChannel
    scope_key: str
    marked_at: str | None = None
    age_seconds: float | None = None
    source_task_uuid: str | None = None
    precise: bool = False
    disease_ids: list[str] = Field(default_factory=list)
    publishable: bool
    blocked_reason: Literal["inactive_country", "disabled_country", "unknown_country", "invalid_scope"] | None = None
    affected_country_count: int = 0


class RealtimePendingPageOut(BaseModel):
    items: list[RealtimePendingOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class RealtimeRunPageOut(BaseModel):
    items: list[RealtimeRunOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class RealtimeActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class RealtimeActionOut(BaseModel):
    channel: RealtimeChannel
    status: Literal["queued", "skipped"]
    task_uuid: str | None = None
    reason: str | None = None
    href: str | None = None


class RealtimeControlPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


class RealtimeControlOut(BaseModel):
    channel: RealtimeChannel
    enabled: bool
    paused: bool
    effective_enabled: bool
    updated_at: str | None = None
    error_code: str | None = None


class RealtimeProblemOut(ProblemDetail):
    task_uuid: str | None = None
    href: str | None = None
