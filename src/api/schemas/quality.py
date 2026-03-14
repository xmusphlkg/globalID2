"""Data quality schemas."""

from typing import List, Optional
from pydantic import BaseModel


class QualityStats(BaseModel):
    total_records: int = 0
    unique_diseases: int = 0
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    zero_cases_count: int = 0
    zero_cases_pct: float = 0.0
    zero_deaths_count: int = 0
    zero_deaths_pct: float = 0.0


class TimeGap(BaseModel):
    month: str
    next_month: Optional[str] = None
    gap_months: float = 0.0


class DataSourceDist(BaseModel):
    data_source: Optional[str] = None
    count: int = 0
    percentage: float = 0.0


class CompletenessItem(BaseModel):
    disease_name: str
    data_months: int = 0
    expected_months: int = 0
    completeness_rate: float = 0.0
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    total_records: int = 0
