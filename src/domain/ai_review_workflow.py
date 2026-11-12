"""Durable business state for literature reviews, separate from model activities."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class AIReviewWorkflow(Base):
    __tablename__ = "ai_review_workflows"

    id: Mapped[int] = mapped_column(
        Integer().with_variant(BigInteger, "postgresql"), primary_key=True
    )
    parent_task_uuid: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.task_uuid", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    step: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ready", server_default="ready"
    )
    outcome: Mapped[str | None] = mapped_column(String(16))
    state = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("ai_work_items.id"))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('articles','summaries')", name="ck_ai_review_workflow_kind"
        ),
        CheckConstraint(
            "status IN ('open','terminal')", name="ck_ai_review_workflow_status"
        ),
        CheckConstraint(
            "step IN ('ready','author','reviewer','terminal')",
            name="ck_ai_review_workflow_step",
        ),
        Index(
            "uq_ai_review_workflow_open_subject",
            "kind",
            "subject_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
        Index(
            "ix_ai_review_workflow_parent",
            "parent_task_uuid",
            "generation",
            "status",
            "id",
        ),
        Index(
            "ix_ai_review_workflow_subject",
            "kind",
            "subject_id",
            "input_fingerprint",
            "policy_version",
        ),
    )


class AIReviewDrain(Base):
    """A finite keyset pass: arrivals beyond high_watermark belong to the next pass."""

    __tablename__ = "ai_review_drains"

    parent_task_uuid: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.task_uuid", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    generation: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=0, server_default="0"
    )
    cursor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scanned: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    admitted: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exhausted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    settings = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    __table_args__ = (
        CheckConstraint(
            "kind IN ('articles','summaries')", name="ck_ai_review_drain_kind"
        ),
    )
