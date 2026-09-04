"""
Knowledge-base models for source-grounded disease and country briefs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class DiseaseKnowledgeSource(BaseModel):
    """A traceable source item used to generate disease knowledge briefs."""

    __tablename__ = "disease_knowledge_sources"

    disease_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="Standard disease ID, e.g. D001")
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, comment="who/who_don/wikidata/wikipedia/msd")
    source_name: Mapped[str] = mapped_column(String(120), nullable=False, comment="Human-readable source name")
    url: Mapped[str] = mapped_column(String(1000), nullable=False, comment="Canonical source URL")
    resolved_url: Mapped[Optional[str]] = mapped_column(String(1000), comment="Final URL after redirects / canonical resolution")
    title: Mapped[Optional[str]] = mapped_column(String(500), comment="Source title")
    license: Mapped[Optional[str]] = mapped_column(String(200), comment="Source license or reuse note")
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False, comment="active/stale/error")
    language: Mapped[str] = mapped_column(String(20), default="en", nullable=False, comment="Source language")
    raw_excerpt: Mapped[Optional[str]] = mapped_column(Text, comment="Short, non-substantial excerpt or metadata summary")
    content_text: Mapped[Optional[str]] = mapped_column(Text, comment="Parsed page text retained for downstream grounding")
    content_sections = Column(JSON, nullable=False, default=list, comment="Parsed page sections and headings")
    raw_excerpt_hash: Mapped[Optional[str]] = mapped_column(String(64), comment="SHA256 of stored excerpt")
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), comment="When the source was fetched")
    review_status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
        comment="pending/approved/rejected/requires_review",
    )
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Adapter-specific metadata")

    __table_args__ = (
        UniqueConstraint("disease_id", "source_type", "url", name="uq_disease_knowledge_source"),
        Index("idx_knowledge_source_disease", "disease_id"),
        Index("idx_knowledge_source_type", "source_type"),
        Index("idx_knowledge_source_review", "review_status"),
    )


class DiseaseKnowledgeBrief(BaseModel):
    """Published or draft AI-generated disease brief grounded in source rows."""

    __tablename__ = "disease_knowledge_briefs"

    disease_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="Standard disease ID")
    language: Mapped[str] = mapped_column(String(20), nullable=False, comment="en/zh")
    brief: Mapped[str] = mapped_column(Text, nullable=False, comment="Short scholarly abstract / lead-in")
    definition: Mapped[Optional[str]] = mapped_column(Text, comment="Disease definition and basic characterization")
    clinical_features: Mapped[Optional[str]] = mapped_column(Text, comment="Clinical manifestations and severity pattern")
    epidemiology: Mapped[Optional[str]] = mapped_column(Text, comment="Distribution, burden, outbreaks, and surveillance context")
    transmission: Mapped[Optional[str]] = mapped_column(Text, comment="Transmission or exposure summary")
    prevention: Mapped[Optional[str]] = mapped_column(Text, comment="Prevention-oriented public-health summary")
    surveillance_note: Mapped[Optional[str]] = mapped_column(Text, comment="How to interpret the page or disease in surveillance context")
    clinical_summary: Mapped[Optional[str]] = mapped_column(Text, comment="Legacy alias for clinical_features")
    risk_groups: Mapped[Optional[str]] = mapped_column(Text, comment="Legacy or source-backed risk groups/populations of concern")
    source_ids = Column(JSON, nullable=False, default=list, comment="Source row IDs used for grounding")
    source_attribution = Column(JSON, nullable=False, default=list, comment="Public source attribution entries")
    disclaimer: Mapped[Optional[str]] = mapped_column(Text, comment="Public information disclaimer")
    model: Mapped[Optional[str]] = mapped_column(String(120), comment="Model used to generate brief")
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, comment="draft/published/requires_review/archived")
    source_confidence: Mapped[str] = mapped_column(String(30), default="medium", nullable=False, comment="high/medium/low")
    quality_score: Mapped[Optional[float]] = mapped_column(Float, comment="0-1 quality score")
    review_notes: Mapped[Optional[str]] = mapped_column(Text, comment="Review notes")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Generator metadata")

    __table_args__ = (
        UniqueConstraint("disease_id", "language", name="uq_disease_knowledge_brief"),
        Index("idx_knowledge_brief_disease", "disease_id"),
        Index("idx_knowledge_brief_language", "language"),
        Index("idx_knowledge_brief_status", "status"),
    )


class CountryBrief(BaseModel):
    """Country page interpretive brief for surveillance data context."""

    __tablename__ = "country_briefs"

    country_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="ISO alpha-2 country code")
    language: Mapped[str] = mapped_column(String(20), nullable=False, comment="en/zh")
    brief: Mapped[Optional[str]] = mapped_column(Text, comment="Short country surveillance introduction")
    surveillance_system: Mapped[Optional[str]] = mapped_column(Text, comment="Surveillance system summary")
    coverage_interpretation: Mapped[Optional[str]] = mapped_column(Text, comment="How to interpret country coverage")
    reporting_cadence: Mapped[Optional[str]] = mapped_column(Text, comment="Reporting cadence explanation")
    data_limitations: Mapped[Optional[str]] = mapped_column(Text, comment="Known data limitations")
    source_summary: Mapped[Optional[str]] = mapped_column(Text, comment="Public source summary")
    status: Mapped[str] = mapped_column(String(30), default="published", nullable=False)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, comment="0-1 quality score")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="Generator metadata")

    __table_args__ = (
        UniqueConstraint("country_code", "language", name="uq_country_brief"),
        Index("idx_country_brief_country", "country_code"),
        Index("idx_country_brief_language", "language"),
        Index("idx_country_brief_status", "status"),
    )
