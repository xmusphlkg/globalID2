"""Persisted scheduler projection shared by control-plane job adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class ScheduledJobState(BaseModel):
    __tablename__ = "scheduled_job_states"

    job_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(100), nullable=False)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(30), nullable=False, default="idle")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_task_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("job_kind", "job_id", name="uq_scheduled_job_state_kind_id"),
        Index("idx_scheduled_job_state_next_run", "next_run_at"),
    )


__all__ = ["ScheduledJobState"]
