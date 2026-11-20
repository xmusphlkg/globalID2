"""Database-backed state for the realtime publication monitor."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class RealtimeMonitorState(BaseModel):
    __tablename__ = "realtime_monitor_states"

    monitor_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    report: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)

