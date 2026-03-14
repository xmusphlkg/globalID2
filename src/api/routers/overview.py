"""Overview router – KPIs, top diseases, trend data.

All queries use parameterised binds to prevent SQL injection.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas.disease_record import OverviewSummary, TopDiseaseItem, TrendPoint
from src.domain.country import Country
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease

router = APIRouter()


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
    latest_str = latest.strftime("%Y-%m-%d") if latest else None

    return OverviewSummary(
        total_diseases=kpi_row.total_diseases or 0,
        total_records=kpi_row.total_records or 0,
        latest_date=latest_str,
        recent_cases_30d=int(kpi_row.recent_cases_30d or 0),
        top_diseases=top_diseases,
    )


@router.get("/overview/trend", response_model=List[TrendPoint])
async def overview_trend(
    country_id: int = Query(..., ge=1),
    disease_code: Optional[str] = Query(None, description="Disease code, e.g. D001. Omit for total (D999)."),
    interval: Optional[int] = Query(None, description="Days to look back. Omit for all time."),
    db: AsyncSession = Depends(get_db),
):
    """Monthly trend data for a given country and (optional) disease."""

    time_period = func.date_trunc("month", DiseaseRecord.time).label("time_period")

    q = (
        select(
            time_period,
            func.sum(DiseaseRecord.cases).label("cases"),
            func.sum(DiseaseRecord.deaths).label("deaths"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(DiseaseRecord.country_id == country_id)
    )

    code = disease_code or "D999"
    q = q.where(Disease.name == code)

    if interval is not None:
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
        )
        for r in rows
    ]
