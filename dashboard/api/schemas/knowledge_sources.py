"""Source-only knowledge collection contracts, separate from brief generation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .operations import TaskReferenceOut

KnowledgeSourceGroup = Literal[
    "who", "who_don", "search", "web", "web_search",
    "wikidata", "wikipedia", "pubmed", "msd",
]


class KnowledgeSourceRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disease_ids: list[str] = Field(min_length=1, max_length=100)
    source: list[KnowledgeSourceGroup] = Field(
        default_factory=list,
        description="Source groups; omitted or empty uses the service's default source adapters.",
    )
    force: bool = False
    priority: Literal["urgent", "high", "normal", "low"] = "normal"

    @field_validator("disease_ids")
    @classmethod
    def normalize_disease_ids(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            value = value.strip().upper()
            if not value or len(value) > 64:
                raise ValueError("Disease IDs must be nonempty and at most 64 characters")
            if value not in normalized:
                normalized.append(value)
        return normalized


class KnowledgeSourceRunSkipped(BaseModel):
    disease_id: str
    reason: str
    task_uuid: str | None = None


class KnowledgeSourceRunResult(BaseModel):
    requested_disease_ids: list[str]
    source_groups: list[str]
    force: bool
    created_count: int
    skipped_count: int
    created_tasks: list[TaskReferenceOut]
    skipped: list[KnowledgeSourceRunSkipped]
