"""Versioned, source-first disease mapping registry models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class SourceDiseaseCategory(BaseModel):
    """Stable identity for one category published by one source.

    Labels are intentionally not identities.  They are mutable evidence attached
    through aliases, while ``category_key`` is derived from source, source code,
    and source definition version.
    """

    __tablename__ = "source_disease_categories"

    category_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    country_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("countries.code", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_code: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_source_label: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_label: Mapped[str] = mapped_column(String(500), nullable=False)
    definition_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default="source-current"
    )
    source_definition: Mapped[Optional[str]] = mapped_column(Text)
    source_definition_uri: Mapped[Optional[str]] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="discovered")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ai_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    ai_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_last_error: Mapped[Optional[str]] = mapped_column(Text)
    ai_last_model: Mapped[Optional[str]] = mapped_column(String(200))
    ai_suggested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ai_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint(
            "source_id", "source_code", "definition_version",
            name="uq_source_disease_category_identity",
        ),
        CheckConstraint(
            "status IN ('discovered','active','retired','rejected')",
            name="ck_source_disease_category_status",
        ),
        CheckConstraint(
            "ai_status IN ('pending','processing','completed','no_model','failed','not_required')",
            name="ck_source_disease_category_ai_status",
        ),
        Index("idx_source_disease_category_country", "country_code"),
        Index("idx_source_disease_category_source", "source_id"),
        Index("idx_source_disease_category_review", "status", "ai_status"),
    )


class SourceDiseaseCategoryAlias(BaseModel):
    """Source-scoped multilingual or historical label for a category."""

    __tablename__ = "source_disease_category_aliases"

    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("source_disease_categories.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(30))
    alias_type: Mapped[str] = mapped_column(String(30), nullable=False, default="observed")
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "category_id", "normalized_alias", "alias_type",
            name="uq_source_disease_category_alias",
        ),
        CheckConstraint(
            "alias_type IN ('observed','official','historical','translation','code_alias')",
            name="ck_source_disease_category_alias_type",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_source_disease_category_alias_validity",
        ),
        Index("idx_source_disease_category_alias_lookup", "normalized_alias"),
    )


class DiseaseMappingAssertion(BaseModel):
    """One reviewable semantic assertion from a source category to a target."""

    __tablename__ = "disease_mapping_assertions_v3"

    assertion_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("source_disease_categories.id", ondelete="CASCADE"), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_code: Mapped[str] = mapped_column(String(120), nullable=False)
    mapping_relation: Mapped[str] = mapped_column(String(30), nullable=False)
    comparability: Mapped[str] = mapped_column(String(30), nullable=False)
    projection_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    aggregation_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    assertion_status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    suggestion_method: Mapped[str] = mapped_column(String(40), nullable=False)
    model_key: Mapped[Optional[str]] = mapped_column(String(200))
    model_version: Mapped[Optional[str]] = mapped_column(String(120))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    evidence = mapped_column(JSON, nullable=False, default=list)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(160))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)
    supersedes_assertion_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("disease_mapping_assertions_v3.id", ondelete="SET NULL")
    )
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("target_kind IN ('concept','group')", name="ck_mapping_v3_target_kind"),
        CheckConstraint(
            "mapping_relation IN ('exact','narrower','broader','aggregate','related','ambiguous','unmapped')",
            name="ck_mapping_v3_relation",
        ),
        CheckConstraint(
            "comparability IN ('direct','conditional','not_comparable','unknown')",
            name="ck_mapping_v3_comparability",
        ),
        CheckConstraint(
            "projection_policy IN ('canonical','discovery_only','no_projection')",
            name="ck_mapping_v3_projection",
        ),
        CheckConstraint(
            "aggregation_policy IN ('direct_only','reported_total','sum_disjoint','non_additive','no_rollup')",
            name="ck_mapping_v3_aggregation",
        ),
        CheckConstraint(
            "assertion_status IN ('proposed','in_review','approved','rejected','superseded')",
            name="ck_mapping_v3_status",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_mapping_v3_confidence",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_mapping_v3_validity",
        ),
        Index("idx_mapping_v3_category", "category_id"),
        Index("idx_mapping_v3_target", "target_kind", "target_code"),
        Index("idx_mapping_v3_review", "assertion_status", "confidence_score"),
    )


class DiseaseMappingCandidate(BaseModel):
    """Machine-generated suggestion; never authoritative until reviewed."""

    __tablename__ = "disease_mapping_candidates_v3"

    candidate_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("source_disease_categories.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    candidate_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_code: Mapped[Optional[str]] = mapped_column(String(120))
    proposed_name_en: Mapped[Optional[str]] = mapped_column(String(300))
    proposed_name_zh: Mapped[Optional[str]] = mapped_column(String(300))
    mapping_relation: Mapped[str] = mapped_column(String(30), nullable=False)
    comparability: Mapped[str] = mapped_column(String(30), nullable=False)
    projection_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    model_key: Mapped[Optional[str]] = mapped_column(String(200))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(80))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    evidence = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "candidate_kind IN ('existing_concept','group','new_concept','unmapped')",
            name="ck_mapping_candidate_v3_kind",
        ),
        CheckConstraint(
            "mapping_relation IN ('exact','narrower','broader','aggregate','related','ambiguous','unmapped')",
            name="ck_mapping_candidate_v3_relation",
        ),
        CheckConstraint(
            "comparability IN ('direct','conditional','not_comparable','unknown')",
            name="ck_mapping_candidate_v3_comparability",
        ),
        CheckConstraint(
            "projection_policy IN ('canonical','discovery_only','no_projection')",
            name="ck_mapping_candidate_v3_projection",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_mapping_candidate_v3_confidence",
        ),
        CheckConstraint(
            "status IN ('proposed','accepted','rejected','stale')",
            name="ck_mapping_candidate_v3_status",
        ),
        Index("idx_mapping_candidate_v3_category", "category_id", "rank"),
        Index("idx_mapping_candidate_v3_review", "status", "confidence_score"),
    )


class DiseaseMappingRelease(BaseModel):
    """Immutable mapping-set release used to reproduce canonical queries."""

    __tablename__ = "disease_mapping_releases_v3"

    release_code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    supersedes_release_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("disease_mapping_releases_v3.id", ondelete="SET NULL")
    )
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','superseded','withdrawn')",
            name="ck_mapping_release_v3_status",
        ),
        Index("idx_mapping_release_v3_status", "status"),
    )


class DiseaseMappingReleaseItem(BaseModel):
    __tablename__ = "disease_mapping_release_items_v3"

    release_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("disease_mapping_releases_v3.id", ondelete="CASCADE"), nullable=False
    )
    assertion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("disease_mapping_assertions_v3.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("release_id", "assertion_id", name="uq_mapping_release_v3_item"),
        Index("idx_mapping_release_v3_item_assertion", "assertion_id"),
    )


class MappingNotificationOutbox(BaseModel):
    """Transactional outbox for mapping alerts; delivery never blocks ingestion."""

    __tablename__ = "mapping_notification_outbox"

    event_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_key: Mapped[str] = mapped_column(String(240), nullable=False)
    recipients = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    provider_response = mapped_column(JSON, nullable=False, default=dict)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    metadata_ = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "provider IN ('auto','smtp','cloudflare')",
            name="ck_mapping_outbox_provider",
        ),
        CheckConstraint(
            "status IN ('pending','sending','retry','sent','skipped','dead')",
            name="ck_mapping_outbox_status",
        ),
        Index("idx_mapping_outbox_delivery", "status", "next_attempt_at"),
        Index("idx_mapping_outbox_aggregate", "aggregate_key"),
    )


__all__ = [
    "SourceDiseaseCategory",
    "SourceDiseaseCategoryAlias",
    "DiseaseMappingCandidate",
    "DiseaseMappingAssertion",
    "DiseaseMappingRelease",
    "DiseaseMappingReleaseItem",
    "MappingNotificationOutbox",
]
