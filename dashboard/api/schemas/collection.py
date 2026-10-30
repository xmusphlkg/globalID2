"""Public metadata contracts for collection and publication."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..routers.ai import DiseaseKnowledgeStartRequest
from ..routers.situation_notices import RefreshRequest
from .literature import LiteratureSyncRequest
from .operations import SourceRunCreate


class SurveillanceRunCreate(SourceRunCreate):
    line: Literal["surveillance"]
    country_code: str = Field(min_length=2, max_length=16)


class LiteratureRunCreate(LiteratureSyncRequest):
    line: Literal["literature"]


class SituationRunCreate(RefreshRequest):
    line: Literal["situation"]


class KnowledgeRunCreate(DiseaseKnowledgeStartRequest):
    line: Literal["knowledge"]


class StageOut(BaseModel):
    stage: str
    attempt: int
    status: str
    metrics: dict[str, Any]
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ArtifactOut(BaseModel):
    attempt: int
    relative_path: str
    sha256: str
    byte_size: int
    source_url: str | None = None
    kind: str


class RunOut(BaseModel):
    task_uuid: str
    task_type: str
    line: str | None
    status: str
    attempt: int
    country_code: str | None = None
    source: str | list[str] | None = None
    stages: list[StageOut]
    artifacts: list[ArtifactOut]
    publication: list[dict[str, Any]]


class LineOut(BaseModel):
    line: str
    schedule_ids: list[str]
    next_run_at: datetime | None = None
    latest_task_uuid: str | None = None
    latest_stage: str | None = None
    latest_stage_status: str | None = None
    pending_publish_count: int


class PublishRunCreate(BaseModel):
    job_id: str = "site-release"
    full: bool = False


class PendingOut(BaseModel):
    surveillance: list[str]
    literature: bool
    situation: bool
    knowledge: list[str]
