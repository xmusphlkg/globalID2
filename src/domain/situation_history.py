"""Models stored in the dedicated Situation Room history database.

These tables intentionally use their own declarative base so importing them
never causes the main application database to create history tables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HistoryBase(DeclarativeBase):
    pass


class HistoryTimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )


class SituationHistorySnapshot(HistoryBase, HistoryTimestampMixin):
    """A durable copy of one immutable content revision."""

    __tablename__ = "situation_history_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    primary_snapshot_id: Mapped[Optional[int]] = mapped_column(Integer)
    snapshot_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_snapshot_id: Mapped[Optional[str]] = mapped_column(String(120))
    generated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[str] = mapped_column(String(40), nullable=False)
    content_updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    data_through: Mapped[Optional[str]] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    operational_status: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_gate_status: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_gate: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    signals: Mapped[list["SituationHistorySignal"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    source_checks: Mapped[list["SituationHistorySourceCheck"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("snapshot_kind", "period_key", "revision", name="uq_history_period_revision"),
        Index("idx_history_snapshot_period", "snapshot_kind", "period_key", "revision"),
        Index("idx_history_snapshot_checked", "checked_at"),
        Index("idx_history_snapshot_quality", "quality_gate_status", "operational_status"),
    )


class SituationHistorySignal(HistoryBase, HistoryTimestampMixin):
    """Flattened signal evidence for fast dashboard search and comparison."""

    __tablename__ = "situation_history_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    history_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("situation_history_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(String(300), nullable=False)
    section: Mapped[str] = mapped_column(String(30), nullable=False)
    disease_id: Mapped[Optional[str]] = mapped_column(String(100))
    disease_name: Mapped[Optional[str]] = mapped_column(String(240))
    country_code: Mapped[Optional[str]] = mapped_column(String(20))
    country_name: Mapped[Optional[str]] = mapped_column(String(240))
    geography: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    series_id: Mapped[Optional[str]] = mapped_column(String(300))
    source_id: Mapped[Optional[str]] = mapped_column(String(300))
    metric_type: Mapped[Optional[str]] = mapped_column(String(120))
    unit: Mapped[Optional[str]] = mapped_column(String(80))
    cadence: Mapped[Optional[str]] = mapped_column(String(30))
    comparison_window: Mapped[Optional[str]] = mapped_column(String(100))
    current_value: Mapped[Optional[float]] = mapped_column(Float)
    previous_value: Mapped[Optional[float]] = mapped_column(Float)
    change_pct: Mapped[Optional[float]] = mapped_column(Float)
    standard_z: Mapped[Optional[float]] = mapped_column(Float)
    robust_z: Mapped[Optional[float]] = mapped_column(Float)
    ewma_value: Mapped[Optional[float]] = mapped_column(Float)
    ewma_alarm: Mapped[Optional[int]] = mapped_column(Integer)
    bayesian_change_probability: Mapped[Optional[float]] = mapped_column(Float)
    detector_votes: Mapped[Optional[int]] = mapped_column(Integer)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_level: Mapped[Optional[str]] = mapped_column(String(30))
    risk_confidence: Mapped[Optional[str]] = mapped_column(String(20))
    risk_dimensions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    missing_dimensions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    data_through: Mapped[Optional[str]] = mapped_column(String(40))
    evidence_url: Mapped[Optional[str]] = mapped_column(String(1500))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[SituationHistorySnapshot] = relationship(back_populates="signals")

    __table_args__ = (
        UniqueConstraint("history_snapshot_id", "section", "signal_id", name="uq_history_signal_section"),
        Index("idx_history_signal_disease", "disease_id", "disease_name"),
        Index("idx_history_signal_country", "country_code", "country_name"),
        Index("idx_history_signal_risk", "risk_level", "risk_score"),
    )


class SituationHistorySourceCheck(HistoryBase, HistoryTimestampMixin):
    """Source health recorded with a specific snapshot check."""

    __tablename__ = "situation_history_source_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    history_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("situation_history_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    checked_at: Mapped[Optional[str]] = mapped_column(String(40))
    item_count: Mapped[Optional[int]] = mapped_column(Integer)
    stale_until: Mapped[Optional[str]] = mapped_column(String(40))
    error: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped[SituationHistorySnapshot] = relationship(back_populates="source_checks")

    __table_args__ = (
        UniqueConstraint(
            "history_snapshot_id", "source", "checked_at", name="uq_history_snapshot_source_check"
        ),
        Index("idx_history_source_status", "source", "status", "checked_at"),
    )


class SituationHistoryAudit(HistoryBase):
    """Append-only audit trail for suppress/correct/restore decisions."""

    __tablename__ = "situation_history_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(300), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[Optional[str]] = mapped_column(String(160))
    note: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    __table_args__ = (
        Index("idx_history_audit_target", "target_type", "target_id", "happened_at"),
        Index("idx_history_audit_action", "action", "happened_at"),
    )


class SituationHistorySyncRun(HistoryBase):
    """Durable record of bootstrap, backfill, and reconciliation runs."""

    __tablename__ = "situation_history_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    mode: Mapped[str] = mapped_column(String(30), nullable=False, default="incremental")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    snapshots_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshots_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signals_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_checks_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("idx_history_sync_started", "started_at", "status"),)


class SituationHistoryReportV3(HistoryBase, HistoryTimestampMixin):
    """Immutable v3 public report archived before publication is advanced."""

    __tablename__ = "situation_history_reports_v3"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String(140), nullable=False, unique=True)
    report_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_gate: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    signals: Mapped[list["SituationHistorySignalV3"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("report_kind", "period_key", "revision", name="uq_history_v3_report_revision"),
        Index("idx_history_v3_report_period", "report_kind", "period_key", "revision"),
        Index("idx_history_v3_report_as_of", "as_of"),
    )


class SituationHistorySignalV3(HistoryBase, HistoryTimestampMixin):
    __tablename__ = "situation_history_signals_v3"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    history_report_id: Mapped[int] = mapped_column(
        ForeignKey("situation_history_reports_v3.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(String(180), nullable=False)
    disease_id: Mapped[str] = mapped_column(String(100), nullable=False)
    country_code: Mapped[Optional[str]] = mapped_column(String(20))
    series_code: Mapped[str] = mapped_column(String(240), nullable=False)
    anomaly_state: Mapped[str] = mapped_column(String(30), nullable=False)
    q_value: Mapped[Optional[float]] = mapped_column(Float)
    review_priority: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    report: Mapped[SituationHistoryReportV3] = relationship(back_populates="signals")

    __table_args__ = (
        UniqueConstraint("history_report_id", "signal_id", name="uq_history_v3_report_signal"),
        Index("idx_history_v3_signal_identity", "disease_id", "country_code", "series_code"),
        Index("idx_history_v3_signal_state", "anomaly_state", "q_value"),
    )
