"""
GlobalID V2 Population Record Model

人口记录模型：存储国家年度人口（WPP）
"""
from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class PopulationRecord(BaseModel):
    """国家年度人口记录。"""

    __tablename__ = "population_records"

    country_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("countries.id", ondelete="CASCADE"),
        nullable=False,
        comment="国家ID",
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, comment="年份")
    population: Mapped[float] = mapped_column(Float, nullable=False, comment="总人口")
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="WPP", comment="数据源")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, comment="额外元数据")

    country: Mapped["Country"] = relationship("Country", back_populates="population_records")

    __table_args__ = (
        UniqueConstraint("country_id", "year", name="uq_population_country_year"),
        Index("idx_population_country", "country_id"),
        Index("idx_population_year", "year"),
        Index("idx_population_country_year", "country_id", "year"),
    )

    def __repr__(self) -> str:
        return (
            f"<PopulationRecord(country_id={self.country_id}, year={self.year}, "
            f"population={self.population})>"
        )
