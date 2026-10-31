"""Durable individual model calls, independent of parent task execution slots."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class AIWorkItem(Base):
    __tablename__ = "ai_work_items"
    id: Mapped[int] = mapped_column(
        Integer().with_variant(BigInteger, "postgresql"), primary_key=True
    )
    parent_task_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    line: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    capability: Mapped[str] = mapped_column(
        String(64), nullable=False, default="text", server_default="text"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    payload = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    result = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    route_key: Mapped[str | None] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','leased','succeeded','failed','cancelled')",
            name="ck_ai_work_items_status",
        ),
        CheckConstraint(
            "priority IN ('urgent','high','normal','low')",
            name="ck_ai_work_items_priority",
        ),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_ai_work_items_attempt"),
        Index(
            "ix_ai_work_items_claim",
            "priority",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_ai_work_items_parent", "parent_task_uuid", "status"),
    )
