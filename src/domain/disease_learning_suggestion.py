"""GlobalID V2 Disease Learning Suggestion Model."""

from typing import Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class DiseaseLearningSuggestion(BaseModel):
    """Unknown disease names discovered during ingestion for manual review."""

    __tablename__ = "disease_learning_suggestions"

    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="CASCADE"),
        nullable=False,
    )
    local_name: Mapped[str] = mapped_column(String(500), nullable=False)

    source_url: Mapped[Optional[str]] = mapped_column(Text)
    context: Mapped[Optional[str]] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)

    suggested_disease_id: Mapped[Optional[str]] = mapped_column(String(100))
    suggested_standard_name: Mapped[Optional[str]] = mapped_column(String(200))
    ai_confidence: Mapped[Optional[float]] = mapped_column(Float)
    ai_reasoning: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)
    final_disease_id: Mapped[Optional[str]] = mapped_column(String(100))
    final_mapping_id: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_learning_country", "country_code"),
        Index("idx_learning_status", "status"),
        Index("idx_learning_occurrence", "occurrence_count"),
        Index("idx_learning_confidence", "ai_confidence"),
        Index("idx_learning_unique_country_local", "country_code", "local_name", unique=True),
    )
