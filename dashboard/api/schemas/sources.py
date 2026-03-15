"""Sources / Data Flow schemas."""

from typing import List, Optional
from pydantic import BaseModel


class StageInfo(BaseModel):
    """One pipeline stage (crawl / process / report / export) for a data source."""

    stage: str  # "crawl" | "process" | "report" | "export"
    task_type: str  # underlying TaskType value
    status: Optional[str] = None  # latest task status, None = never run
    task_uuid: Optional[str] = None
    task_name: Optional[str] = None
    progress: int = 0
    last_run: Optional[str] = None  # ISO datetime string


class DataSourceFlow(BaseModel):
    """Full pipeline view for one data source inside a country."""

    data_source: str
    record_count: int = 0
    latest_date: Optional[str] = None
    stages: List[StageInfo] = []
