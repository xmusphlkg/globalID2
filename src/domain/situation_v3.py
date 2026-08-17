"""Normalized operational persistence for Situation Room v3."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class SituationAnalysisRunV3(BaseModel):
    __tablename__ = "situation_analysis_runs_v3"

    run_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    timings = mapped_column(JSON, nullable=False, default=dict)
    coverage = mapped_column(JSON, nullable=False, default=dict)
    quality_gate = mapped_column(JSON, nullable=False, default=dict)
    ledger_summary = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)

    signal_results: Mapped[list["SituationSignalResultV3"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_situation_v3_run_checked", "checked_at"),
        Index("idx_situation_v3_run_status", "status", "checked_at"),
        Index("idx_situation_v3_run_input", "input_hash"),
    )


class SituationSignalResultV3(BaseModel):
    __tablename__ = "situation_signal_results_v3"

    run_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("situation_analysis_runs_v3.run_id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    disease_id: Mapped[Optional[str]] = mapped_column(String(100))
    country_code: Mapped[Optional[str]] = mapped_column(String(20))
    canonical_geography_key: Mapped[Optional[str]] = mapped_column(String(300))
    series_code: Mapped[Optional[str]] = mapped_column(String(240))
    source_system: Mapped[Optional[str]] = mapped_column(String(180))
    metric_type: Mapped[Optional[str]] = mapped_column(String(120))
    cadence: Mapped[Optional[str]] = mapped_column(String(30))
    raw_p_value: Mapped[Optional[float]] = mapped_column(Float)
    q_value: Mapped[Optional[float]] = mapped_column(Float)
    anomaly_state: Mapped[Optional[str]] = mapped_column(String(30))
    review_priority: Mapped[Optional[str]] = mapped_column(String(30))
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(120))
    payload = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[SituationAnalysisRunV3] = relationship(back_populates="signal_results")

    __table_args__ = (
        UniqueConstraint("run_id", "signal_id", name="uq_situation_v3_run_signal"),
        Index("idx_situation_v3_signal_run_state", "run_id", "anomaly_state"),
        Index("idx_situation_v3_signal_identity", "disease_id", "country_code", "series_code"),
        Index("idx_situation_v3_signal_q", "q_value", "anomaly_state"),
    )


class SituationEventClusterV3(BaseModel):
    __tablename__ = "situation_event_clusters_v3"

    cluster_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    disease_id: Mapped[str] = mapped_column(String(100), nullable=False)
    disease_name: Mapped[str] = mapped_column(String(300), nullable=False)
    geographies = mapped_column(JSON, nullable=False, default=list)
    first_published_at: Mapped[str] = mapped_column(String(40), nullable=False)
    last_published_at: Mapped[str] = mapped_column(String(40), nullable=False)
    source_state: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    review_state: Mapped[str] = mapped_column(String(30), nullable=False, default="unreviewed")
    corrected_payload = mapped_column(JSON, nullable=False, default=dict)

    items: Mapped[list["SituationEventClusterItemV3"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_situation_v3_event_disease", "disease_id", "last_published_at"),
        Index("idx_situation_v3_event_review", "review_state", "last_published_at"),
    )


class SituationEventClusterItemV3(BaseModel):
    __tablename__ = "situation_event_cluster_items_v3"

    cluster_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("situation_event_clusters_v3.cluster_id", ondelete="CASCADE"), nullable=False
    )
    update_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1500), nullable=False)
    published_at: Mapped[str] = mapped_column(String(40), nullable=False)
    payload = mapped_column(JSON, nullable=False, default=dict)

    cluster: Mapped[SituationEventClusterV3] = relationship(back_populates="items")

    __table_args__ = (Index("idx_situation_v3_event_item_cluster", "cluster_id", "published_at"),)


class SituationPeriodReportV3(BaseModel):
    __tablename__ = "situation_period_reports_v3"

    report_id: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)
    report_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[str] = mapped_column(String(40), nullable=False)
    period_end: Mapped[str] = mapped_column(String(40), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    supersedes_report_id: Mapped[Optional[str]] = mapped_column(String(140))
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_gate = mapped_column(JSON, nullable=False, default=dict)
    coverage = mapped_column(JSON, nullable=False, default=dict)
    payload = mapped_column(JSON, nullable=False, default=dict)

    members: Mapped[list["SituationReportMemberV3"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("report_kind", "period_key", "revision", name="uq_situation_v3_report_revision"),
        Index("idx_situation_v3_report_period", "report_kind", "period_key", "revision"),
        Index("idx_situation_v3_report_status", "status", "as_of"),
    )


class SituationReportMemberV3(BaseModel):
    __tablename__ = "situation_report_members_v3"

    report_id: Mapped[str] = mapped_column(
        String(140), ForeignKey("situation_period_reports_v3.report_id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("situation_analysis_runs_v3.run_id", ondelete="RESTRICT"), nullable=False
    )

    report: Mapped[SituationPeriodReportV3] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("report_id", "run_id", name="uq_situation_v3_report_member"),
        Index("idx_situation_v3_report_member_run", "run_id"),
    )


class SituationReviewDecisionV3(BaseModel):
    __tablename__ = "situation_review_decisions_v3"

    decision_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(180), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[Optional[str]] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(Text, nullable=False)
    payload = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("idx_situation_v3_review_target", "target_type", "target_id", "created_at"),)


class SituationPublicationPointerV3(BaseModel):
    __tablename__ = "situation_publication_pointers_v3"

    channel: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    report_id: Mapped[str] = mapped_column(
        String(140), ForeignKey("situation_period_reports_v3.report_id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_report_id: Mapped[Optional[str]] = mapped_column(String(140))

    __table_args__ = (Index("idx_situation_v3_pointer_report", "report_id"),)

