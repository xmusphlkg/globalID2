"""Persistence for the notice-based Situation module.

The module is built on official notices from international public-health
authorities (WHO, ECDC, Africa CDC, PAHO).  Every public claim traces back to a
stored notice, and every AI output is stored with the model that wrote it, the
model that peer-reviewed it, and the prompt version both used.

Publication is automatic: an AI output is public only once an independent
reviewer model accepted it (``status == "published"``).  No human gate exists.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class SituationSourceRun(BaseModel):
    """One collection attempt against one authority feed."""

    __tablename__ = "situation_source_runs"

    run_id: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("run_id", "source_id", name="uq_situation_source_run"),
        Index("idx_situation_source_run_source", "source_id", "started_at"),
    )


class SituationNotice(BaseModel):
    """One official notice, stored as metadata plus a short attributed extract."""

    __tablename__ = "situation_notices"

    notice_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source_id: Mapped[str] = mapped_column(String(40), nullable=False)
    native_id: Mapped[str] = mapped_column(String(200), nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title_en: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary_en: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1500), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    redistribution: Mapped[str] = mapped_column(String(40), nullable=False)
    published_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at_source: Mapped[Optional[str]] = mapped_column(String(40))
    iso_week_key: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Hash of the fields the AI reads; a change re-queues the notice for AI.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: pending -> enriched | rejected | failed; drives the AI work queue.
    ai_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    ai_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_last_error: Mapped[Optional[str]] = mapped_column(Text)
    ai_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    entities: Mapped[list["SituationNoticeEntity"]] = relationship(
        back_populates="notice", cascade="all, delete-orphan"
    )
    assessments: Mapped[list["SituationAIAssessment"]] = relationship(
        back_populates="notice", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source_id", "native_id", name="uq_situation_notice_source_native"),
        Index("idx_situation_notice_published", "published_date"),
        Index("idx_situation_notice_week", "iso_week_key"),
        Index("idx_situation_notice_ai_queue", "ai_status", "ai_next_attempt_at"),
    )


class SituationNoticeEntity(BaseModel):
    """A disease or jurisdiction a notice is about, with how that was decided."""

    __tablename__ = "situation_notice_entities"

    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # disease | country
    entity_id: Mapped[str] = mapped_column(String(40), nullable=False)  # D050 | CD
    name_en: Mapped[Optional[str]] = mapped_column(String(240))
    name_zh: Mapped[Optional[str]] = mapped_column(String(240))
    #: rule (catalogue match) or ai (peer-reviewed extraction).
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    #: False once a peer-reviewed AI extraction disagrees with a rule match.
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    notice: Mapped[SituationNotice] = relationship(back_populates="entities")

    __table_args__ = (
        UniqueConstraint("notice_id", "entity_type", "entity_id", name="uq_situation_notice_entity"),
        Index("idx_situation_notice_entity_lookup", "entity_type", "entity_id", "active"),
    )


class SituationAIAssessment(BaseModel):
    """Peer-reviewed AI enrichment of one notice.

    ``author_*`` wrote the draft, ``reviewer_*`` audited it independently.  Only
    a ``published`` row is exported; earlier versions stay as ``superseded``.
    """

    __tablename__ = "situation_ai_assessments"

    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # published | rejected | superseded
    title_zh: Mapped[Optional[str]] = mapped_column(String(1000))
    title_fr: Mapped[Optional[str]] = mapped_column(String(1000))
    summary_en: Mapped[Optional[str]] = mapped_column(Text)
    summary_zh: Mapped[Optional[str]] = mapped_column(Text)
    summary_fr: Mapped[Optional[str]] = mapped_column(Text)
    event_type: Mapped[Optional[str]] = mapped_column(String(40))
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))
    risk_rationale: Mapped[Optional[str]] = mapped_column(Text)
    disease_ids = mapped_column(JSON, nullable=False, default=list)
    country_codes = mapped_column(JSON, nullable=False, default=list)
    author_model: Mapped[Optional[str]] = mapped_column(String(200))
    reviewer_model: Mapped[Optional[str]] = mapped_column(String(200))
    review_verdict: Mapped[Optional[str]] = mapped_column(String(20))  # accept | revise | reject
    review_notes = mapped_column(JSON, nullable=False, default=list)
    review_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    notice: Mapped[SituationNotice] = relationship(back_populates="assessments")

    __table_args__ = (
        Index("idx_situation_ai_assessment_notice", "notice_id", "status"),
    )


class SituationEvent(BaseModel):
    """A cross-notice event: one disease in one place over time."""

    __tablename__ = "situation_events"

    event_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    disease_id: Mapped[str] = mapped_column(String(40), nullable=False)
    country_codes = mapped_column(JSON, nullable=False, default=list)
    first_published: Mapped[date] = mapped_column(Date, nullable=False)
    last_published: Mapped[date] = mapped_column(Date, nullable=False)
    notice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_ids = mapped_column(JSON, nullable=False, default=list)
    #: Highest peer-reviewed risk level among the member notices.
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | quiet

    members: Mapped[list["SituationEventNotice"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_situation_event_disease", "disease_id", "last_published"),
        Index("idx_situation_event_status", "status", "last_published"),
    )


class SituationEventNotice(BaseModel):
    __tablename__ = "situation_event_notices"

    event_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_events.event_id", ondelete="CASCADE"), nullable=False
    )
    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )

    event: Mapped[SituationEvent] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("event_id", "notice_id", name="uq_situation_event_notice"),
    )


class SituationDigest(BaseModel):
    """Peer-reviewed weekly situation narrative.  Every claim cites notice ids."""

    __tablename__ = "situation_digests"

    period_key: Mapped[str] = mapped_column(String(10), nullable=False)  # 2026-W43
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # published | rejected | superseded
    headline_en: Mapped[Optional[str]] = mapped_column(String(500))
    headline_zh: Mapped[Optional[str]] = mapped_column(String(500))
    headline_fr: Mapped[Optional[str]] = mapped_column(String(500))
    #: [{"text_en", "text_zh", "text_fr", "notice_ids": [...], "event_ids": [...]}]
    points = mapped_column(JSON, nullable=False, default=list)
    notice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    author_model: Mapped[Optional[str]] = mapped_column(String(200))
    reviewer_model: Mapped[Optional[str]] = mapped_column(String(200))
    review_verdict: Mapped[Optional[str]] = mapped_column(String(20))
    review_notes = mapped_column(JSON, nullable=False, default=list)
    review_score: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_situation_digest_period", "period_key", "status"),
    )
