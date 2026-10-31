"""Data-line collection models.

One business run is still one ``tasks`` row.  Each execution attempt leaves one
``RunStage`` per stage (``collect``/``organize``/``store``), the bytes each
connector downloaded are recorded in ``SourceArtifact`` before they are parsed,
and a successful ``store`` marks the affected scope ``PublishDirty`` so the next
``EXPORT_DATA`` task recomputes only that scope.

The ``ai_work_items`` model queue is a separate concern (migration 0023).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

STAGE_NAMES = ("collect", "organize", "store")
STAGE_STATUSES = ("running", "completed", "failed", "skipped")
LINES = ("surveillance", "literature", "situation", "knowledge")

# BigInt on PostgreSQL (BIGSERIAL) but plain INTEGER on SQLite, where only an
# exact ``INTEGER PRIMARY KEY`` auto-increments.  The migration keeps BIGSERIAL
# for production; this variant lets the same model run under the in-memory
# SQLite used by tests.
BIG_PK = Integer().with_variant(BigInteger, "postgresql")
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class RunStage(Base):
    """One stage of one execution attempt of one task."""

    __tablename__ = "run_stages"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    task_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict, server_default=text("'{}'")
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("task_uuid", "stage", "attempt", name="uq_run_stages_attempt"),
        CheckConstraint(
            "stage IN ('collect', 'organize', 'store')", name="ck_run_stages_name"
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name="ck_run_stages_status",
        ),
        Index("ix_run_stages_task", "task_uuid", "attempt"),
    )


class SourceArtifact(Base):
    """Raw bytes a connector downloaded, kept on disk under ``data/raw/``."""

    __tablename__ = "source_artifacts"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    task_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="collect", server_default="collect"
    )
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_ = mapped_column(
        "metadata",
        JSON_DOCUMENT,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (Index("ix_source_artifacts_task", "task_uuid", "attempt"),)


class PublishDirty(Base):
    """A scope whose formal rows changed but whose projections are unpublished."""

    __tablename__ = "publish_dirty"

    id: Mapped[int] = mapped_column(BIG_PK, primary_key=True, autoincrement=True)
    line: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_task_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_task_uuid: Mapped[str | None] = mapped_column(String(36))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "line IN ('surveillance', 'literature', 'situation', 'knowledge')",
            name="ck_publish_dirty_line",
        ),
        Index(
            "ix_publish_dirty_pending",
            "line",
            "scope_key",
            "marked_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )


__all__ = ["PublishDirty", "RunStage", "SourceArtifact"]
