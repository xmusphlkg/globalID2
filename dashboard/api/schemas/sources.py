"""Sources / Data Flow schemas."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from ..location_codes import COUNTRY_REGION_CODE_MAX_LENGTH


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
    country_id: Optional[int] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    record_count: int = 0
    source_series_count: int = 0
    source_observation_count: int = 0
    series_availability: Dict[str, int] = Field(default_factory=dict)
    source_availability: Dict[str, int] = Field(default_factory=dict)
    observation_quality: Dict[str, int] = Field(default_factory=dict)
    metric_types: Dict[str, int] = Field(default_factory=dict)
    mapping_relations: Dict[str, int] = Field(default_factory=dict)
    comparability: Dict[str, int] = Field(default_factory=dict)
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    history_start_year: Optional[int] = None
    source_scope: Optional[str] = None
    latest_task_uuid: Optional[str] = None
    latest_task_source: Optional[str] = None
    latest_task_status: Optional[str] = None
    latest_task_time: Optional[str] = None
    stages: List[StageInfo] = Field(default_factory=list)


class SourcePolicyOut(BaseModel):
    supports_current_month: bool = False
    default_include_current_month: bool = False
    dynamic_revision_enabled: bool = False
    default_revision_window: int = 3
    default_revision_window_months: int = 3
    revision_window_unit: str = "months"
    temporal_granularity: str = "monthly"
    current_month_status: str = "not_supported"
    public_release_enabled: bool = True
    public_release_editable: bool = False
    publication_day: Optional[int] = None
    source_update_cadence: Optional[str] = None


class SourceOptionOut(BaseModel):
    value: str
    label_en: str
    label_zh: str
    label: str
    source_kind: str = "current"
    supports_start_year: bool = False
    default_start_year: Optional[int] = None
    history_end_year: Optional[int] = None
    supports_fill_missing: bool = False
    default_fill_missing: bool = False
    source_policy: Optional[SourcePolicyOut] = None


class CountrySourceConfigOut(BaseModel):
    country_code: str
    country_name: str
    country_name_en: str
    country_name_zh: str
    language: str
    timezone: str
    supports_crawl: bool
    supports_fill_missing: bool
    default_fill_missing: bool
    default_source: str
    default_start_year: Optional[int] = None
    supports_start_year: bool = False
    supports_source_file: bool = False
    supports_source_dir: bool = False
    source_policy: SourcePolicyOut = Field(default_factory=SourcePolicyOut)
    source_options: List[SourceOptionOut] = Field(default_factory=list)


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
    include_current_month: bool
    revision_window_months: int
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
    admin_emails: List[str] = Field(default_factory=list)
    email_enabled: bool = False
    last_tick_at: Optional[str] = None
    jobs: List[AutomationJobOut] = Field(default_factory=list)


class AutomationTriggerResult(BaseModel):
    job_id: str
    status: str
    task_uuid: Optional[str] = None
    reason: Optional[str] = None


class AutomationJobCreate(BaseModel):
    job_id: str
    name: str
    country_code: str = Field(
        min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH
    )
    source: str = "all"
    enabled: bool = True
    priority: str = "normal"
    process: bool = True
    save_raw: bool = True
    fill_missing: bool = False
    force: bool = False
    include_current_month: Optional[bool] = None
    revision_window_months: int = Field(3, ge=1, le=52)
    retry_threshold: int = 3
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


class AutomationJobUpdate(BaseModel):
    name: Optional[str] = None
    country_code: Optional[str] = Field(
        None, min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH
    )
    source: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[str] = None
    process: Optional[bool] = None
    save_raw: Optional[bool] = None
    fill_missing: Optional[bool] = None
    force: Optional[bool] = None
    include_current_month: Optional[bool] = None
    revision_window_months: Optional[int] = Field(None, ge=1, le=52)
    retry_threshold: Optional[int] = None
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
