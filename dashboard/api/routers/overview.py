"""Overview router – KPIs, top diseases, trend data.

All queries use parameterised binds to prevent SQL injection.
"""

from datetime import date, datetime, time
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..frequency import infer_country_frequency
from ..schemas.disease_record import MonthlyComparisonPoint, OverviewSummary, TopDiseaseItem, TrendPoint
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease

router = APIRouter()


async def _country_has_total_disease(country_id: int, db: AsyncSession) -> bool:
    q = (
        select(func.count())
        .select_from(DiseaseRecord)
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(DiseaseRecord.country_id == country_id, Disease.name == "D999")
    )
    count = (await db.execute(q)).scalar_one()
    return bool(count)


@router.get("/overview/summary", response_model=OverviewSummary)
async def overview_summary(
    country_id: int = Query(..., ge=1),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    """Single aggregated call that powers the Overview page KPIs + top diseases."""

    # --- KPI metrics (single query) ---
    kpi_q = select(
        func.count(func.distinct(DiseaseRecord.disease_id)).label("total_diseases"),
        func.count().label("total_records"),
        func.min(DiseaseRecord.time).label("earliest_date"),
        func.max(DiseaseRecord.time).label("latest_date"),
        func.coalesce(
            func.sum(DiseaseRecord.cases).filter(
                DiseaseRecord.time > func.now() - text("INTERVAL '30 days'")
            ),
            0,
        ).label("recent_cases_30d"),
    ).where(DiseaseRecord.country_id == country_id)

    kpi_row = (await db.execute(kpi_q)).one()

    # --- Top 10 diseases in last 365 days ---
    if lang == "zh":
        name_col = func.coalesce(
            StandardDisease.standard_name_zh, Disease.name_en, Disease.name
        ).label("name")
    else:
        name_col = func.coalesce(Disease.name_en, Disease.name).label("name")

    top_q = (
        select(
            name_col,
            Disease.name_en.label("name_en"),
            func.sum(DiseaseRecord.cases).label("total_cases"),
            func.sum(DiseaseRecord.deaths).label("total_deaths"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
        .where(
            DiseaseRecord.country_id == country_id,
            DiseaseRecord.time > func.now() - text("INTERVAL '365 days'"),
        )
        .group_by(name_col, Disease.name_en)
        .order_by(func.sum(DiseaseRecord.cases).desc())
        .limit(10)
    )
    top_rows = (await db.execute(top_q)).all()

    top_diseases = [
        TopDiseaseItem(
            name=r.name or "",
            name_en=r.name_en,
            total_cases=int(r.total_cases or 0),
            total_deaths=int(r.total_deaths or 0),
        )
        for r in top_rows
    ]

    latest = kpi_row.latest_date
    earliest = kpi_row.earliest_date
    latest_str = latest.strftime("%Y-%m-%d") if latest else None
    earliest_str = earliest.strftime("%Y-%m-%d") if earliest else None

    return OverviewSummary(
        total_diseases=kpi_row.total_diseases or 0,
        total_records=kpi_row.total_records or 0,
        earliest_date=earliest_str,
        latest_date=latest_str,
        recent_cases_30d=int(kpi_row.recent_cases_30d or 0),
        top_diseases=top_diseases,
    )


@router.get("/overview/trend", response_model=List[TrendPoint])
async def overview_trend(
    country_id: int = Query(..., ge=1),
    disease_code: Optional[str] = Query(None, description="Disease code, e.g. D001. Omit for total (D999)."),
    interval: Optional[int] = Query(None, description="Days to look back. Omit for all time."),
    start_date: Optional[date] = Query(None, description="Inclusive start date. Overrides interval when present."),
    end_date: Optional[date] = Query(None, description="Inclusive end date. Overrides interval when present."),
    db: AsyncSession = Depends(get_db),
):
    """Monthly trend data for a given country and (optional) disease."""

    bucket = await infer_country_frequency(country_id, db)
    time_period = func.date_trunc(bucket, DiseaseRecord.time).label("time_period")

    q = (
        select(
            time_period,
            func.sum(DiseaseRecord.cases).label("cases"),
            func.sum(DiseaseRecord.deaths).label("deaths"),
            func.avg(DiseaseRecord.incidence_rate)
            .filter(DiseaseRecord.incidence_rate >= 0)
            .label("incidence_rate"),
            func.avg(DiseaseRecord.mortality_rate)
            .filter(DiseaseRecord.mortality_rate >= 0)
            .label("mortality_rate"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(DiseaseRecord.country_id == country_id)
    )

    if disease_code:
        q = q.where(Disease.name == disease_code)
    else:
        has_total = await _country_has_total_disease(country_id, db)
        if has_total:
            q = q.where(Disease.name == "D999")

    if start_date is not None:
        q = q.where(DiseaseRecord.time >= datetime.combine(start_date, time.min))
    if end_date is not None:
        q = q.where(DiseaseRecord.time <= datetime.combine(end_date, time.max))

    if interval is not None and start_date is None and end_date is None:
        q = q.where(
            DiseaseRecord.time > func.now() - text(f"INTERVAL '{int(interval)} days'")
        )

    q = q.group_by(time_period).order_by(time_period)
    rows = (await db.execute(q)).all()

    return [
        TrendPoint(
            time_period=r.time_period.strftime("%Y-%m-%d") if r.time_period else "",
            cases=int(r.cases or 0),
            deaths=int(r.deaths or 0),
            incidence_rate=round(float(r.incidence_rate), 4) if r.incidence_rate is not None else None,
            mortality_rate=round(float(r.mortality_rate), 4) if r.mortality_rate is not None else None,
        )
        for r in rows
    ]


@router.get("/overview/monthly-comparison", response_model=List[MonthlyComparisonPoint])
async def overview_monthly_comparison(
    country_id: int = Query(..., ge=1),
    disease_code: Optional[str] = Query(None, description="Disease code, e.g. D001. Omit for total (D999)."),
    interval: Optional[int] = Query(None, description="Days to look back. Omit for all time."),
    start_date: Optional[date] = Query(None, description="Inclusive start date. Overrides interval when present."),
    end_date: Optional[date] = Query(None, description="Inclusive end date. Overrides interval when present."),
    db: AsyncSession = Depends(get_db),
):
    """Year-by-month comparison for seasonality and structural shifts."""

    year_part = func.extract("year", DiseaseRecord.time).label("year")
    month_part = func.extract("month", DiseaseRecord.time).label("month")

    q = (
        select(
            year_part,
            month_part,
            func.sum(DiseaseRecord.cases).label("cases"),
            func.sum(DiseaseRecord.deaths).label("deaths"),
            func.avg(DiseaseRecord.incidence_rate)
            .filter(DiseaseRecord.incidence_rate >= 0)
            .label("incidence_rate"),
            func.avg(DiseaseRecord.mortality_rate)
            .filter(DiseaseRecord.mortality_rate >= 0)
            .label("mortality_rate"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(DiseaseRecord.country_id == country_id)
    )

    if disease_code:
        q = q.where(Disease.name == disease_code)
    else:
        has_total = await _country_has_total_disease(country_id, db)
        if has_total:
            q = q.where(Disease.name == "D999")

    if start_date is not None:
        q = q.where(DiseaseRecord.time >= datetime.combine(start_date, time.min))
    if end_date is not None:
        q = q.where(DiseaseRecord.time <= datetime.combine(end_date, time.max))

    if interval is not None and start_date is None and end_date is None:
        q = q.where(
            DiseaseRecord.time > func.now() - text(f"INTERVAL '{int(interval)} days'")
        )

    q = q.group_by(year_part, month_part).order_by(year_part, month_part)
    rows = (await db.execute(q)).all()

    return [
        MonthlyComparisonPoint(
            year=int(r.year),
            month=int(r.month),
            cases=int(r.cases or 0),
            deaths=int(r.deaths or 0),
            incidence_rate=round(float(r.incidence_rate), 4) if r.incidence_rate is not None else None,
            mortality_rate=round(float(r.mortality_rate), 4) if r.mortality_rate is not None else None,
        )
        for r in rows
    ]
