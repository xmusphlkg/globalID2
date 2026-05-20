"""Disease record schemas."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator

from src.core.missing_values import normalize_rate_value


class DiseaseRecordOut(BaseModel):
    time: datetime
    disease_id: int
    country_id: int
    cases: Optional[int] = None
    deaths: Optional[int] = None
    recoveries: Optional[int] = None
    active_cases: Optional[int] = None
    new_cases: Optional[int] = None
    new_deaths: Optional[int] = None
    new_recoveries: Optional[int] = None
    incidence_rate: Optional[float] = None
    mortality_rate: Optional[float] = None
    recovery_rate: Optional[float] = None
    region: Optional[str] = None
    city: Optional[str] = None
    data_source: Optional[str] = None
    data_quality: Optional[str] = None
    confidence_score: Optional[float] = None

    @field_validator("incidence_rate", "mortality_rate", mode="before")
    @classmethod
    def _normalize_missing_rates(cls, value: object) -> Optional[float]:
        return normalize_rate_value(value)

    model_config = {"from_attributes": True}


class OverviewSummary(BaseModel):
    total_diseases: int = 0
    total_records: int = 0
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    recent_cases_30d: int = 0
    top_diseases: list = []


class TopDiseaseItem(BaseModel):
    name: str
    name_en: Optional[str] = None
    total_cases: int = 0
    total_deaths: int = 0


class TrendPoint(BaseModel):
    time_period: str
    cases: int = 0
    deaths: int = 0
