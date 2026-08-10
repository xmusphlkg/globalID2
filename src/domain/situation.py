"""Persistence models for the public Situation Room."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class PublicHealthEvent(BaseModel):
    """A compact, attributable external public-health event.

    Article bodies are deliberately not stored here.  The record keeps only
    normalized metadata, a short source-provided title, and provenance.
    """

    __tablename__ = "public_health_events"

    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_at: Mapped[Optional[str]] = mapped_column(String(40))
    updated_at_source: Mapped[Optional[str]] = mapped_column(String(40))
    disease_id: Mapped[Optional[str]] = mapped_column(String(100))
    disease_name: Mapped[Optional[str]] = mapped_column(String(240))
    geographies = mapped_column(JSON, nullable=False, default=list)
    agency_risk: Mapped[Optional[str]] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    confidence: Mapped[str] = mapped_column(String(20), nullable=False, default="low")
    event_key: Mapped[Optional[str]] = mapped_column(String(500))
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_public_health_event_source_external", "source", "external_id", unique=True),
        Index("idx_public_health_event_status_published", "status", "published_at"),
        Index("idx_public_health_event_key", "event_key"),
    )


class SituationSnapshot(BaseModel):
    """A versioned public payload; weekly rows are immutable archives."""

    __tablename__ = "situation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    snapshot_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    iso_week: Mapped[str] = mapped_column(String(12), nullable=False)
    generated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    data_through: Mapped[Optional[str]] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="published")
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    supersedes_snapshot_id: Mapped[Optional[str]] = mapped_column(String(120))
    payload = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_situation_snapshot_kind_week", "snapshot_kind", "iso_week"),
        Index("idx_situation_snapshot_status_created", "status", "created_at"),
    )


class SituationOverride(BaseModel):
    """An auditable human decision affecting an event or snapshot."""

    __tablename__ = "situation_overrides"

    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    actor: Mapped[Optional[str]] = mapped_column(String(160))
    payload = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_situation_override_target", "target_type", "target_id"),
    )
