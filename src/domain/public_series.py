"""Published disease curves read by the public site.

``disease_series_observations`` stays the ingestion ledger. These tables store
the already-projected country/disease series so a crawl does not make the site
rescan that ledger.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class PublicDiseaseSeries(Base, TimestampMixin):
    """One public time series for a country and a standard disease."""

    __tablename__ = "public_disease_series"

    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="CASCADE"),
        primary_key=True,
    )
    disease_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"),
        primary_key=True,
    )
    period_starts: Mapped[list[date]] = mapped_column(ARRAY(Date), nullable=False)
    cases: Mapped[list[Optional[int]]] = mapped_column(ARRAY(Integer), nullable=False)
    deaths: Mapped[list[Optional[int]]] = mapped_column(ARRAY(Integer), nullable=False)
    incidence: Mapped[list[Optional[float]]] = mapped_column(
        ARRAY(DOUBLE_PRECISION), nullable=False
    )
    granularity: Mapped[str] = mapped_column(String(40), nullable=False)
    data_layer: Mapped[str] = mapped_column(String(40), nullable=False)
    source_series_code: Mapped[Optional[str]] = mapped_column(String(180))
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cases: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_deaths: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_high_water: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    projection_version: Mapped[str] = mapped_column(String(40), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "cardinality(period_starts) = cardinality(cases) "
            "AND cardinality(period_starts) = cardinality(deaths) "
            "AND cardinality(period_starts) = cardinality(incidence)",
            name="ck_public_disease_series_array_lengths",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_public_disease_series_content_hash",
        ),
        Index("idx_public_disease_series_disease", "disease_id", "country_code"),
    )


class CountryPublishState(Base, TimestampMixin):
    """Watermark for one jurisdiction's published disease curves."""

    __tablename__ = "country_publish_state"

    country_code: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("countries.code", ondelete="CASCADE"),
        primary_key=True,
    )
    source_high_water: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    series_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_version: Mapped[str] = mapped_column(String(40), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_country_publish_state_content_hash",
        ),
    )
