"""Country scope model for language/variant mapping context."""

from typing import Optional

from sqlalchemy import Boolean, Column, ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class CountryScope(BaseModel):
    """Scope rows split canonical countries from mapping variants (e.g. CN_EN)."""

    __tablename__ = "country_scopes"

    scope_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False, default="canonical")
    language_code: Mapped[Optional[str]] = mapped_column(String(20))
    display_name: Mapped[Optional[str]] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_country_scope_code", "scope_code"),
        Index("idx_country_scope_country", "country_code"),
        Index("idx_country_scope_type", "scope_type"),
        Index("idx_country_scope_active", "is_active"),
    )
