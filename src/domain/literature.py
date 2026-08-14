"""Research Radar persistence models.

The literature domain deliberately stores source metadata and editorial state
separately.  Raw abstracts are retained for classification only; public site
exports are built from bibliographic metadata and reviewed GIDS summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class LiteratureArticle(BaseModel):
    __tablename__ = "literature_articles"

    article_id: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    doi: Mapped[Optional[str]] = mapped_column(String(300), unique=True)
    pmid: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    pmcid: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    openalex_id: Mapped[Optional[str]] = mapped_column(String(80), unique=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    journal: Mapped[Optional[str]] = mapped_column(String(500))
    issn: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    publisher: Mapped[Optional[str]] = mapped_column(String(500))
    authors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    article_type: Mapped[str] = mapped_column(String(80), nullable=False, default="journal-article")
    study_type: Mapped[Optional[str]] = mapped_column(String(120))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    abstract_text: Mapped[Optional[str]] = mapped_column(Text)
    abstract_license: Mapped[Optional[str]] = mapped_column(String(500))
    source_urls: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    open_access_status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    open_access_url: Mapped[Optional[str]] = mapped_column(String(2000))
    license_url: Mapped[Optional[str]] = mapped_column(String(2000))
    peer_review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="peer_reviewed")
    integrity_status: Mapped[str] = mapped_column(String(40), nullable=False, default="current")

    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    public_health_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discovery_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    publication_status: Mapped[str] = mapped_column(String(40), nullable=False, default="review")
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_literature_article_published", "published_at"),
        Index("idx_literature_article_status", "publication_status"),
        Index("idx_literature_article_discovery", "discovery_score"),
        Index("idx_literature_article_integrity", "integrity_status"),
    )


class LiteratureDiseaseLink(BaseModel):
    __tablename__ = "literature_disease_links"

    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    disease_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    match_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("article_id", "disease_id", name="uq_literature_article_disease"),
        Index("idx_literature_disease_link_disease", "disease_id"),
    )


class LiteratureCountryLink(BaseModel):
    __tablename__ = "literature_country_links"

    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    country_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country_name: Mapped[str] = mapped_column(String(200), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("article_id", "country_code", name="uq_literature_article_country"),
        Index("idx_literature_country_link_country", "country_code"),
    )


class LiteratureTopicLink(BaseModel):
    __tablename__ = "literature_topic_links"

    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        UniqueConstraint("article_id", "topic", name="uq_literature_article_topic"),
        Index("idx_literature_topic_link_topic", "topic"),
    )


class LiteratureSummary(BaseModel):
    __tablename__ = "literature_summaries"

    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(12), nullable=False)
    research_question: Mapped[Optional[str]] = mapped_column(Text)
    study_design: Mapped[Optional[str]] = mapped_column(Text)
    population_setting: Mapped[Optional[str]] = mapped_column(Text)
    main_findings: Mapped[Optional[str]] = mapped_column(Text)
    public_health_relevance: Mapped[Optional[str]] = mapped_column(Text)
    limitations: Mapped[Optional[str]] = mapped_column(Text)
    gids_interpretation: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    generated_by: Mapped[Optional[str]] = mapped_column(String(120))
    model: Mapped[Optional[str]] = mapped_column(String(160))
    provider: Mapped[Optional[str]] = mapped_column(String(80))
    quality_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generation_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("article_id", "language", name="uq_literature_summary_language"),
        Index("idx_literature_summary_status", "status"),
    )


class LiteratureStatusEvent(BaseModel):
    __tablename__ = "literature_status_events"

    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    previous_status: Mapped[Optional[str]] = mapped_column(String(60))
    current_status: Mapped[str] = mapped_column(String(60), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    effective_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (Index("idx_literature_status_event_article", "article_id"),)


class LiteratureIngestRun(BaseModel):
    __tablename__ = "literature_ingest_runs"

    run_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    from_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    through_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_literature_ingest_run_started", "started_at"),
        Index("idx_literature_ingest_run_status", "status"),
    )


class LiteratureEvidenceGap(BaseModel):
    """Persistent lifecycle for one active signal's literature coverage gap."""

    __tablename__ = "literature_evidence_gaps"

    gap_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    signal_id: Mapped[str] = mapped_column(String(160), nullable=False)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(200))
    signal_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    signal_section: Mapped[str] = mapped_column(String(60), nullable=False)
    disease_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"), nullable=False
    )
    disease_name: Mapped[str] = mapped_column(String(300), nullable=False)
    country_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    country_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gap_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    query_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latest_metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_searched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_search_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("signal_id", "disease_id", name="uq_literature_gap_signal_disease"),
        Index("idx_literature_gap_status_priority", "status", "priority_score"),
        Index("idx_literature_gap_disease", "disease_id"),
        Index("idx_literature_gap_next_search", "next_search_at"),
    )


class LiteratureSignalArticleLink(BaseModel):
    """Reviewable evidence relationship between a signal and an article."""

    __tablename__ = "literature_signal_article_links"

    gap_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("literature_evidence_gaps.gap_id", ondelete="SET NULL")
    )
    signal_id: Mapped[str] = mapped_column(String(160), nullable=False)
    article_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False
    )
    relation_level: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="review")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    match_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(160))
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("signal_id", "article_id", name="uq_literature_signal_article"),
        Index("idx_literature_signal_article_status", "status"),
        Index("idx_literature_signal_article_gap", "gap_id"),
        Index("idx_literature_signal_article_article", "article_id"),
    )


__all__ = [
    "LiteratureArticle",
    "LiteratureCountryLink",
    "LiteratureDiseaseLink",
    "LiteratureEvidenceGap",
    "LiteratureIngestRun",
    "LiteratureSignalArticleLink",
    "LiteratureStatusEvent",
    "LiteratureSummary",
    "LiteratureTopicLink",
]
