"""Stable public contracts for the control-center shell."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ResponseMeta(BaseModel):
    request_id: str | None = None
    pagination: "PaginationMeta | None" = None


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class DataResponse(BaseModel, Generic[T]):
    data: T
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    instance: str | None = None
    request_id: str | None = None
    field_errors: list[dict[str, Any]] | None = None


class RuntimeServiceOut(BaseModel):
    service: str
    instance_id: str
    status: str
    host: str
    pid: int
    last_seen_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeSummaryOut(BaseModel):
    heartbeat_available: bool
    services: list[RuntimeServiceOut]


class ActionItemOut(BaseModel):
    id: str
    severity: str
    category: str
    title: str
    detail: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    href: str


class RecentTaskOut(BaseModel):
    task_uuid: str
    name: str
    type: str
    status: str
    progress: int
    created_at: datetime
    last_error: str | None = None


class PipelineStageOut(BaseModel):
    id: str
    label: str
    status: str


class ControlPlaneOverviewOut(BaseModel):
    generated_at: datetime
    tasks: dict[str, int]
    schedules: dict[str, int]
    runtime: RuntimeSummaryOut
    action_items: list[ActionItemOut]
    recent_tasks: list[RecentTaskOut]
    pipeline: list[PipelineStageOut]


ResponseMeta.model_rebuild()

__all__ = [
    "ActionItemOut",
    "ControlPlaneOverviewOut",
    "DataResponse",
    "PaginationMeta",
    "ProblemDetail",
    "ResponseMeta",
    "RuntimeServiceOut",
    "RuntimeSummaryOut",
]
