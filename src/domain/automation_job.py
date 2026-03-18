"""Persistent automation job configuration."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class AutomationJob(BaseModel):
    __tablename__ = "automation_jobs"

    job_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="all")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    save_raw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fill_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_automation_jobs_enabled", "enabled"),
        Index("idx_automation_jobs_country", "country_code"),
    )
