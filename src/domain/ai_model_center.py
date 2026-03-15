"""AI model center domain models."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class AIProviderConfig(BaseModel):
    """Provider-level API configuration for model routing."""

    __tablename__ = "ai_provider_configs"

    provider_key: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
        comment="Unique provider key, e.g. qianwen-prod",
    )
    provider_name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="Provider family, e.g. openai/qianwen/glm/anthropic/custom",
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="Display name")
    api_style: Mapped[str] = mapped_column(
        String(50),
        default="openai_compatible",
        nullable=False,
        comment="API call style: openai_compatible / anthropic",
    )
    base_url: Mapped[Optional[str]] = mapped_column(String(500), comment="Provider base URL")
    api_key: Mapped[Optional[str]] = mapped_column(Text, comment="API key (stored as plain text)")
    organization: Mapped[Optional[str]] = mapped_column(String(200), comment="Optional org/tenant")

    extra_headers = mapped_column(JSON, nullable=False, default=dict, comment="Extra headers")
    extra_config = mapped_column(JSON, nullable=False, default=dict, comment="Provider config")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    last_check_status: Mapped[str] = mapped_column(
        String(30), default="unknown", nullable=False, comment="unknown/available/unavailable"
    )
    last_check_message: Mapped[Optional[str]] = mapped_column(Text, comment="Last health check message")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    models: Mapped[List["AIModelConfig"]] = relationship(
        "AIModelConfig",
        back_populates="provider",
        cascade="all, delete-orphan",
        order_by="AIModelConfig.priority",
    )

    __table_args__ = (
        Index("idx_ai_provider_active", "is_active"),
        Index("idx_ai_provider_name", "provider_name"),
        Index("idx_ai_provider_priority", "priority"),
    )


class AIModelConfig(BaseModel):
    """Model-level routing and status config."""

    __tablename__ = "ai_models"

    provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ai_provider_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_key: Mapped[str] = mapped_column(
        String(200),
        unique=True,
        nullable=False,
        comment="Unique model key, e.g. qianwen-prod:qwen-plus",
    )
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(50), default="chat", nullable=False)
    api_style: Mapped[Optional[str]] = mapped_column(String(50), comment="Override provider api_style")

    temperature: Mapped[Optional[float]] = mapped_column(comment="Optional model-level temperature")
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, comment="Optional model-level max_tokens")
    extra_params = mapped_column(JSON, nullable=False, default=dict, comment="Model-level params")

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    last_check_status: Mapped[str] = mapped_column(
        String(30), default="unknown", nullable=False, comment="unknown/available/unavailable"
    )
    last_check_message: Mapped[Optional[str]] = mapped_column(Text, comment="Last health check message")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    provider: Mapped[AIProviderConfig] = relationship("AIProviderConfig", back_populates="models")

    __table_args__ = (
        Index("idx_ai_model_enabled", "is_enabled"),
        Index("idx_ai_model_priority", "priority"),
        Index("idx_ai_model_provider", "provider_id"),
    )
