"""Data quality router – stats, gaps, source distribution, completeness."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.quality import CompletenessItem, DataSourceDist, QualityStats, TimeGap
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease

router = APIRouter()


@router.get("/quality/stats", response_model=QualityStats)
async def quality_stats(
    country_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    q = select(
        func.count().label("total_records"),
        func.count(func.distinct(DiseaseRecord.disease_id)).label("unique_diseases"),
        func.min(DiseaseRecord.time).label("earliest_date"),
        func.max(DiseaseRecord.time).label("latest_date"),
        func.count(case((DiseaseRecord.cases == 0, 1))).label("zero_cases_count"),
        func.count(case((DiseaseRecord.deaths == 0, 1))).label("zero_deaths_count"),
    ).where(DiseaseRecord.country_id == country_id)

    row = (await db.execute(q)).one()
    total = row.total_records or 1  # avoid division by zero

    return QualityStats(
        total_records=row.total_records or 0,
        unique_diseases=row.unique_diseases or 0,
        earliest_date=row.earliest_date.strftime("%Y-%m-%d") if row.earliest_date else None,
        latest_date=row.latest_date.strftime("%Y-%m-%d") if row.latest_date else None,
        zero_cases_count=row.zero_cases_count or 0,
        zero_cases_pct=round((row.zero_cases_count or 0) / total * 100, 2),
        zero_deaths_count=row.zero_deaths_count or 0,
        zero_deaths_pct=round((row.zero_deaths_count or 0) / total * 100, 2),
    )


@router.get("/quality/gaps", response_model=List[TimeGap])
async def quality_gaps(
    country_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Find gaps > 1 month in the time-series."""
    raw = text("""
        WITH months AS (
            SELECT DISTINCT date_trunc('month', time) AS month
            FROM disease_records
            WHERE country_id = :cid
            ORDER BY month
        )
        SELECT month,
               LEAD(month) OVER (ORDER BY month) AS next_month,
               EXTRACT(EPOCH FROM (LEAD(month) OVER (ORDER BY month) - month)) / 2592000 AS gap_months
        FROM months
    """)
    rows = (await db.execute(raw, {"cid": country_id})).all()
    return [
        TimeGap(
            month=r.month.strftime("%Y-%m-%d") if r.month else "",
            next_month=r.next_month.strftime("%Y-%m-%d") if r.next_month else None,
            gap_months=round(float(r.gap_months or 0), 2),
        )
        for r in rows
        if r.gap_months and float(r.gap_months) > 1.0
    ]


@router.get("/quality/sources", response_model=List[DataSourceDist])
async def quality_sources(
    country_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    total_sub = (
        select(func.count())
        .where(DiseaseRecord.country_id == country_id)
        .correlate(None)
        .scalar_subquery()
    )
    q = (
        select(
            DiseaseRecord.data_source,
            func.count().label("count"),
            func.round(func.count() * 100.0 / total_sub, 2).label("percentage"),
        )
        .where(DiseaseRecord.country_id == country_id)
        .group_by(DiseaseRecord.data_source)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(q)).all()
    return [
        DataSourceDist(data_source=r.data_source, count=r.count, percentage=float(r.percentage or 0))
        for r in rows
    ]


@router.get("/quality/completeness", response_model=List[CompletenessItem])
async def quality_completeness(
    country_id: int = Query(..., ge=1),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    if lang == "zh":
        name_col = func.coalesce(
            StandardDisease.standard_name_zh, Disease.name_en, Disease.name
        ).label("disease_name")
    else:
        name_col = func.coalesce(Disease.name_en, Disease.name).label("disease_name")

    q = (
        select(
            name_col,
            func.count(func.distinct(func.date_trunc("month", DiseaseRecord.time))).label("data_months"),
            (
                (func.extract("year", func.max(DiseaseRecord.time)) - func.extract("year", func.min(DiseaseRecord.time))) * 12
                + (func.extract("month", func.max(DiseaseRecord.time)) - func.extract("month", func.min(DiseaseRecord.time)))
            ).label("total_months_span"),
            func.min(DiseaseRecord.time).label("earliest_date"),
            func.max(DiseaseRecord.time).label("latest_date"),
            func.count().label("total_records"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
        .where(DiseaseRecord.country_id == country_id)
        .group_by(Disease.id, name_col)
        .order_by(name_col)
    )

    if start:
        q = q.where(DiseaseRecord.time >= start)
    if end:
        q = q.where(DiseaseRecord.time <= end)

    rows = (await db.execute(q)).all()

    result = []
    for r in rows:
        span = float(r.total_months_span or 0)
        expected = max(int(span) + 1, 1)
        data_m = int(r.data_months or 0)
        rate = round(data_m / expected * 100, 2) if expected else 0.0
        result.append(
            CompletenessItem(
                disease_name=r.disease_name or "",
                data_months=data_m,
                expected_months=expected,
                completeness_rate=rate,
                earliest_date=r.earliest_date.strftime("%Y-%m-%d") if r.earliest_date else None,
                latest_date=r.latest_date.strftime("%Y-%m-%d") if r.latest_date else None,
                total_records=r.total_records or 0,
            )
        )
    return result
