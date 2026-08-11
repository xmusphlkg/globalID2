"""Contracts for tasks, schedules, and release resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskReferenceOut(BaseModel):
    task_uuid: str | None = None
    status: str
    resource_type: str = "task"
    resource_id: str | None = None
    href: str


class TaskEventOut(BaseModel):
    id: str
    type: str
    title: str
    content: str | None = None
    success: bool
    error: str | None = None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleOut(BaseModel):
    id: str
    kind: str
    job_id: str
    name: str
    enabled: bool
    country_code: str | None = None
    timezone: str | None = None
    interval_minutes: int | None = None
    daily_time: str | None = None
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str
    last_error: str | None = None
    last_task_uuid: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class ScheduleRunOut(BaseModel):
    task_uuid: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None


class SourceOut(BaseModel):
    id: str
    country_code: str
    name: str
    enabled: bool
    schedule_count: int
    enabled_schedule_count: int
    last_task_uuid: str | None = None


class SourceRunCreate(BaseModel):
    source: str = "all"
    force: bool = False
    process: bool = True
    save_raw: bool = True
    fill_missing: bool = False
    include_current_month: bool | None = None
    revision_window_months: int | None = Field(default=None, ge=1, le=52)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    source_file: str | None = None
    source_dir: str | None = None
    priority: str = "normal"


__all__ = [
    "ScheduleOut",
    "ScheduleRunOut",
    "SourceOut",
    "SourceRunCreate",
    "TaskEventOut",
    "TaskReferenceOut",
]
