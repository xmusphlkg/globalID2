"""Task schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskOut(BaseModel):
    id: int
    task_uuid: str
    task_name: str
    task_type: str
    status: str
    priority: str
    progress: int = 0
    country_id: Optional[int] = None
    report_id: Optional[int] = None
    description: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    actual_duration: Optional[float] = None
    workbook_count: int = 0
    cancel_requested: bool = False
    cancel_requested_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TaskDetailOut(TaskOut):
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    parent_task_id: Optional[int] = None
    workbook_entries: List["WorkbookEntryOut"] = []


class WorkbookEntryOut(BaseModel):
    id: int
    entry_uuid: Optional[str] = None
    entry_type: str
    title: str
    content: Optional[str] = None
    content_type: Optional[str] = None
    prompt: Optional[str] = None
    response: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    cost: Optional[float] = None
    duration: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskCreateRequest(BaseModel):
    task_type: str
    task_name: str
    country_id: Optional[int] = None
    priority: str = "normal"
    description: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None


class TaskStatusUpdate(BaseModel):
    """WebSocket message for task status updates."""
    task_uuid: str
    status: str
    progress: int = 0
    last_error: Optional[str] = None
    updated_at: str
