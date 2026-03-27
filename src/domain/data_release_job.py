"""Persistent data-release workflow configuration."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class DataReleaseJob(BaseModel):
    __tablename__ = "data_release_jobs"

    job_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="high")
    auto_after_crawls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_git_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_cloudflare_deploy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_clean_worktree: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    github_remote: Mapped[str] = mapped_column(String(100), nullable=False, default="origin")
    github_branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudflare_project_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commit_message_template: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="chore(data-release): publish site data {timestamp}",
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_data_release_jobs_enabled", "enabled"),
        Index("idx_data_release_jobs_job_id", "job_id"),
    )
