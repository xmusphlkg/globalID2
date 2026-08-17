"""Control-plane contracts for Research Radar editorial operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


PublicationStatus = Literal["review", "published", "excluded"]
SummaryStatus = Literal["draft", "review", "published", "rejected"]


class LiteratureSummaryOut(BaseModel):
    language: str
    research_question: str | None = None
    study_design: str | None = None
    population_setting: str | None = None
    main_findings: str | None = None
    public_health_relevance: str | None = None
    limitations: str | None = None
    gids_interpretation: str | None = None
    status: str
    generated_by: str | None = None
    model: str | None = None
    provider: str | None = None
    quality_score: float | None = None
    evidence_map: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime | None = None
    review_notes: str | None = None


class LiteratureArticleOut(BaseModel):
    article_id: str
    slug: str
    doi: str | None = None
    pmid: str | None = None
    title: str
    journal: str | None = None
    publisher: str | None = None
    authors: list[dict[str, str]] = Field(default_factory=list)
    article_type: str
    study_type: str | None = None
    published_at: datetime | None = None
    indexed_at: datetime | None = None
    open_access_status: str
    peer_review_status: str
    integrity_status: str
    relevance_score: float
    public_health_score: float
    discovery_score: float
    publication_status: str
    is_featured: bool
    diseases: list[dict[str, Any]] = Field(default_factory=list)
    countries: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)
    summaries: list[LiteratureSummaryOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LiteratureArticleUpdate(BaseModel):
    publication_status: PublicationStatus | None = None
    is_featured: bool | None = None
    editorial_note: str | None = Field(default=None, max_length=2000)
    summary_language: Literal["en", "zh"] | None = None
    summary: dict[str, str | None] | None = None
    summary_status: SummaryStatus | None = None


class LiteratureIngestRunOut(BaseModel):
    run_uuid: str
    source: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    from_indexed_at: datetime | None = None
    through_indexed_at: datetime | None = None
    counts: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LiteratureDashboardOut(BaseModel):
    total_articles: int
    published_articles: int
    review_queue: int
    excluded_articles: int
    featured_articles: int
    published_last_7_days: int
    summaries_awaiting_review: int
    surveillance_context: dict[str, Any] = Field(default_factory=dict)
    latest_articles: list[LiteratureArticleOut]
    latest_runs: list[LiteratureIngestRunOut]
    schedule: dict[str, Any]
    automation: dict[str, Any] = Field(default_factory=dict)


class LiteratureAutomationRequest(BaseModel):
    dry_run: bool = False
    export: bool = True


class LiteratureEnrichmentRequest(BaseModel):
    article_ids: list[str] = Field(default_factory=list, max_length=50)
    languages: list[Literal["en", "zh"]] = Field(default_factory=lambda: ["en", "zh"])
    limit: int | None = Field(default=None, ge=1, le=50)
    force: bool = False


class LiteratureSyncRequest(BaseModel):
    since: datetime | None = None


class LiteratureSyncOut(BaseModel):
    task_uuid: str | None = None
    status: str
    reason: str | None = None


class LiteratureGapCandidateOut(BaseModel):
    id: int
    gap_id: str | None = None
    signal_id: str
    article_id: str
    article_slug: str
    article_title: str
    journal: str | None = None
    published_at: datetime | None = None
    publication_status: str
    integrity_status: str
    relation_level: str
    status: str
    confidence: float
    source: str
    match_reasons: list[str] = Field(default_factory=list)
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_note: str | None = None


class LiteratureEvidenceGapOut(BaseModel):
    gap_id: str
    signal_id: str
    snapshot_id: str | None = None
    signal_kind: str
    signal_section: str
    disease_id: str
    disease_name: str
    country_codes: list[str] = Field(default_factory=list)
    country_names: list[str] = Field(default_factory=list)
    gap_type: str
    status: str
    priority_score: float
    query_plan: dict[str, Any] = Field(default_factory=dict)
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    source_snapshot_at: datetime | None = None
    first_detected_at: datetime
    last_detected_at: datetime
    last_searched_at: datetime | None = None
    next_search_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    error: str | None = None
    candidates: list[LiteratureGapCandidateOut] = Field(default_factory=list)


class LiteratureGapDiscoveryRequest(BaseModel):
    gap_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int | None = Field(default=None, ge=1, le=50)


class LiteratureGapUpdate(BaseModel):
    status: Literal["open", "dismissed"]
    note: str | None = Field(default=None, max_length=2000)


class LiteratureEvidenceLinkReview(BaseModel):
    status: Literal["confirmed", "rejected"]
    relation_level: Literal["exact_disease_geography", "disease_context", "candidate"] | None = None
    note: str | None = Field(default=None, max_length=2000)


__all__ = [
    "LiteratureArticleOut",
    "LiteratureArticleUpdate",
    "LiteratureAutomationRequest",
    "LiteratureDashboardOut",
    "LiteratureEnrichmentRequest",
    "LiteratureEvidenceGapOut",
    "LiteratureEvidenceLinkReview",
    "LiteratureGapCandidateOut",
    "LiteratureGapDiscoveryRequest",
    "LiteratureGapUpdate",
    "LiteratureIngestRunOut",
    "LiteratureSyncOut",
    "LiteratureSyncRequest",
    "LiteratureSummaryOut",
]
