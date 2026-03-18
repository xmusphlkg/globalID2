"""Sources / Data Flow schemas."""

from typing import Any, List, Optional
from pydantic import BaseModel


class StageInfo(BaseModel):
    """One data-ingestion pipeline stage for a data source."""

    stage: str  # "fetch_list" | "incremental_check" | "process_store" | "finalize"
    task_type: str  # underlying task type (currently crawl_data)
    status: Optional[str] = None  # latest task status, None = never run
    task_uuid: Optional[str] = None
    task_name: Optional[str] = None
    progress: int = 0
    last_run: Optional[str] = None  # ISO datetime string


class DataSourceFlow(BaseModel):
    """Full data-ingestion flow for one data source inside a country."""

    data_source: str
    record_count: int = 0
    latest_date: Optional[str] = None
    latest_task_uuid: Optional[str] = None
    latest_task_source: Optional[str] = None
    latest_task_status: Optional[str] = None
    latest_task_time: Optional[str] = None
    stages: List[StageInfo] = []


class AutomationJobOut(BaseModel):
    job_id: str
    name: str
    country_code: str
    source: str
    enabled: bool
    priority: str
    process: bool
    save_raw: bool
    fill_missing: bool
    force: bool
    retry_threshold: int
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
    next_run_at: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_status: str
    last_error: Optional[str] = None
    last_task_uuid: Optional[str] = None
    run_count: int = 0
    skipped_count: int = 0


class AutomationConfigOut(BaseModel):
    enabled: bool
    timezone: str
    poll_interval_seconds: int
    default_retry_threshold: int
    admin_emails: List[str] = []
    email_enabled: bool = False
    last_tick_at: Optional[str] = None
    jobs: List[AutomationJobOut] = []


class AutomationTriggerResult(BaseModel):
    job_id: str
    status: str
    task_uuid: Optional[str] = None
    reason: Optional[str] = None


class AutomationJobCreate(BaseModel):
    job_id: str
    name: str
    country_code: str
    source: str = "all"
    enabled: bool = True
    priority: str = "normal"
    process: bool = True
    save_raw: bool = True
    fill_missing: bool = True
    force: bool = False
    retry_threshold: int = 3
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


class AutomationJobUpdate(BaseModel):
    name: Optional[str] = None
    country_code: Optional[str] = None
    source: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[str] = None
    process: Optional[bool] = None
    save_raw: Optional[bool] = None
    fill_missing: Optional[bool] = None
    force: Optional[bool] = None
    retry_threshold: Optional[int] = None
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
