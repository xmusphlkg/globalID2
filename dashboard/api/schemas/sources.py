"""Sources / Data Flow schemas."""

from typing import List, Optional
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
