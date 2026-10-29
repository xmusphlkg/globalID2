"""Persistence for the alert-based Situation module.

The module reads official reports in full and extracts *alerts*: one disease in
one place, with the counts and dates the report states and one sentence quoted
from it.  Tables, from raw material to published map data:

* ``situation_notices``: one official report (metadata and its AI work queue);
* ``situation_documents``: the report's full text, kept internal only;
* ``situation_extractions``: one peer-reviewed reading of one document version;
* ``situation_alerts``: the alerts that reading produced (the map points);
* ``situation_outbreaks``: alerts of one disease grouped into one outbreak.

Publication is automatic.  An extraction is public only once an independent
reviewer model accepted it (``status == "published"``).  No human gate exists.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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
    """One official report and the state of its extraction queue."""

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
    #: Hash of the catalogue fields; a change re-queues the report.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: pending -> extracted | rejected | failed; drives the AI work queue.
    ai_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    ai_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_last_error: Mapped[Optional[str]] = mapped_column(Text)
    ai_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    document: Mapped[Optional["SituationDocument"]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", uselist=False
    )
    extractions: Mapped[list["SituationExtraction"]] = relationship(
        back_populates="notice", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["SituationAlert"]] = relationship(back_populates="notice", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_id", "native_id", name="uq_situation_notice_source_native"),
        Index("idx_situation_notice_published", "published_date"),
        Index("idx_situation_notice_week", "iso_week_key"),
        Index("idx_situation_notice_ai_queue", "ai_status", "ai_next_attempt_at"),
    )


class SituationDocument(BaseModel):
    """The full text of a report.  Internal analysis input; never published."""

    __tablename__ = "situation_documents"

    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: How to obtain the document: ``{"kind": "inline_html" | "pdf_page" | "dspace", ...}``.
    ref = mapped_column(JSON, nullable=False, default=dict)
    #: pending -> ready | failed | unavailable
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    media_type: Mapped[Optional[str]] = mapped_column(String(60))
    text: Mapped[Optional[str]] = mapped_column(Text)
    text_hash: Mapped[Optional[str]] = mapped_column(String(64))
    byte_size: Mapped[Optional[int]] = mapped_column(Integer)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    #: Path of the original file under the document cache, when one was kept.
    cache_path: Mapped[Optional[str]] = mapped_column(String(400))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)

    notice: Mapped[SituationNotice] = relationship(back_populates="document")

    __table_args__ = (Index("idx_situation_document_queue", "status", "next_attempt_at"),)


class SituationExtraction(BaseModel):
    """One peer-reviewed reading of one document version.

    ``author_*`` wrote the draft, ``reviewer_*`` audited it independently.  Only
    a ``published`` row feeds the map; earlier readings stay as ``superseded``.
    """

    __tablename__ = "situation_extractions"

    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # published | rejected | superseded
    #: event_report | monitoring_product | other
    document_type: Mapped[Optional[str]] = mapped_column(String(30))
    #: native_file | text: what the models actually received.
    input_mode: Mapped[Optional[str]] = mapped_column(String(20))
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    author_model: Mapped[Optional[str]] = mapped_column(String(200))
    reviewer_model: Mapped[Optional[str]] = mapped_column(String(200))
    review_verdict: Mapped[Optional[str]] = mapped_column(String(20))
    review_notes = mapped_column(JSON, nullable=False, default=list)
    review_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_score: Mapped[Optional[float]] = mapped_column(Float)

    notice: Mapped[SituationNotice] = relationship(back_populates="extractions")
    alerts: Mapped[list["SituationAlert"]] = relationship(back_populates="extraction", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_situation_extraction_notice", "notice_id", "status"),)


class SituationAlert(BaseModel):
    """One disease in one place, as one report states it."""

    __tablename__ = "situation_alerts"

    alert_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    notice_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )
    extraction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("situation_extractions.id", ondelete="CASCADE"), nullable=False
    )
    outbreak_id: Mapped[Optional[str]] = mapped_column(String(80))
    #: Catalogue id; ``None`` when the report names an agent the catalogue lacks.
    disease_id: Mapped[Optional[str]] = mapped_column(String(40))
    disease_label: Mapped[str] = mapped_column(String(240), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    admin_area: Mapped[Optional[str]] = mapped_column(String(240))
    #: new | ongoing | imported | ended | routine
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    cases: Mapped[Optional[int]] = mapped_column(BigInteger)
    deaths: Mapped[Optional[int]] = mapped_column(BigInteger)
    #: confirmed | probable | suspected | total
    count_basis: Mapped[Optional[str]] = mapped_column(String(20))
    as_of: Mapped[Optional[date]] = mapped_column(Date)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    summary_en: Mapped[Optional[str]] = mapped_column(Text)
    summary_zh: Mapped[Optional[str]] = mapped_column(Text)
    summary_fr: Mapped[Optional[str]] = mapped_column(Text)
    published_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_id: Mapped[str] = mapped_column(String(40), nullable=False)

    notice: Mapped[SituationNotice] = relationship(back_populates="alerts")
    extraction: Mapped[SituationExtraction] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("idx_situation_alert_place", "country_code", "disease_id", "published_date"),
        Index("idx_situation_alert_outbreak", "outbreak_id", "published_date"),
        Index("idx_situation_alert_notice", "notice_id"),
    )


class SituationOutbreak(BaseModel):
    """Alerts of one disease that belong to one outbreak, across places."""

    __tablename__ = "situation_outbreaks"

    outbreak_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    disease_id: Mapped[Optional[str]] = mapped_column(String(40))
    disease_label: Mapped[str] = mapped_column(String(240), nullable=False)
    first_published: Mapped[date] = mapped_column(Date, nullable=False)
    last_published: Mapped[date] = mapped_column(Date, nullable=False)
    country_codes = mapped_column(JSON, nullable=False, default=list)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_ids = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | quiet

    __table_args__ = (
        Index("idx_situation_outbreak_disease", "disease_id", "last_published"),
        Index("idx_situation_outbreak_status", "status", "last_published"),
    )
