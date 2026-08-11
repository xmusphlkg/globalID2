"""Data quality router – stats, gaps, source distribution, completeness."""

from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..frequency import (
    expected_periods,
    infer_country_frequency_profile,
    infer_frequency_profile_from_times,
    period_gap,
    period_start,
)
from ..schemas.quality import CompletenessItem, DataSourceDist, QualityStats, TimeGap
from src.core.source_scopes import canonical_data_source_label
from src.domain.disease import Disease
from src.domain.country import Country
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease

router = APIRouter()


async def _country_id(country_code: str, db: AsyncSession) -> int:
    country_id = (
        await db.execute(
            select(Country.id).where(func.upper(Country.code) == country_code.strip().upper())
        )
    ).scalar_one_or_none()
    if country_id is None:
        raise HTTPException(404, "Country not found")
    return int(country_id)


@router.get("/quality/stats", response_model=QualityStats)
async def quality_stats(
    country_code: str = Query(..., min_length=2, max_length=10),
    db: AsyncSession = Depends(get_db),
):
    country_id = await _country_id(country_code, db)
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
    country_code: str = Query(..., min_length=2, max_length=10),
    db: AsyncSession = Depends(get_db),
):
    """Find gaps > 1 detected period in the country time-series."""
    country_id = await _country_id(country_code, db)
    profile = await infer_country_frequency_profile(country_id, db)
    q = (
        select(DiseaseRecord.time)
        .where(DiseaseRecord.country_id == country_id)
        .distinct()
        .order_by(DiseaseRecord.time)
    )
    rows = (await db.execute(q)).all()
    periods = sorted(
        {period_start(row.time, profile) for row in rows if row.time is not None}
    )

    return [
        TimeGap(
            period_start=current.strftime("%Y-%m-%d"),
            next_period=next_period.strftime("%Y-%m-%d"),
            gap_periods=float(period_gap(current, next_period, profile)),
            period_unit=profile.period_unit,
        )
        for current, next_period in zip(periods, periods[1:])
        if period_gap(current, next_period, profile) > 1
    ]


@router.get("/quality/sources", response_model=List[DataSourceDist])
async def quality_sources(
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    db: AsyncSession = Depends(get_db),
):
    country_id = await _country_id(country_code, db) if country_code else None
    q = select(
        DiseaseRecord.data_source,
        func.count().label("count"),
    ).group_by(DiseaseRecord.data_source)
    if country_id is not None:
        q = q.where(DiseaseRecord.country_id == country_id)
    rows = (await db.execute(q)).all()

    merged: dict[str, int] = {}
    total = 0
    for row in rows:
        label = canonical_data_source_label(row.data_source)
        count = int(row.count or 0)
        merged[label] = merged.get(label, 0) + count
        total += count

    ordered = sorted(merged.items(), key=lambda item: (-item[1], item[0].lower()))
    return [
        DataSourceDist(
            data_source=label,
            count=count,
            percentage=round((count / total) * 100, 2) if total else 0.0,
        )
        for label, count in ordered
    ]


@router.get("/quality/completeness", response_model=List[CompletenessItem])
async def quality_completeness(
    country_code: str = Query(..., min_length=2, max_length=10),
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    country_id = await _country_id(country_code, db)
    if lang == "zh":
        name_col = func.coalesce(
            StandardDisease.standard_name_zh, Disease.name_en, Disease.name
        ).label("disease_name")
    else:
        name_col = func.coalesce(Disease.name_en, Disease.name).label("disease_name")

    q = (
        select(
            Disease.id.label("disease_id"),
            name_col,
            DiseaseRecord.time.label("record_time"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
        .where(DiseaseRecord.country_id == country_id)
        .order_by(name_col, DiseaseRecord.time)
    )

    if start:
        q = q.where(DiseaseRecord.time >= start)
    if end:
        q = q.where(DiseaseRecord.time <= end)

    rows = (await db.execute(q)).all()
    grouped: dict[tuple[int, str], list] = defaultdict(list)
    for row in rows:
        grouped[(int(row.disease_id), row.disease_name or "")].append(row.record_time)

    result = []
    for (_, disease_name), times in sorted(grouped.items(), key=lambda item: item[0][1].lower()):
        profile = infer_frequency_profile_from_times(times)
        period_starts = sorted({period_start(ts, profile) for ts in times if ts is not None})
        earliest_date = min(times) if times else None
        latest_date = max(times) if times else None
        earliest_period = period_starts[0] if period_starts else None
        latest_period = period_starts[-1] if period_starts else None
        data_periods = len(period_starts)
        expected = max(expected_periods(earliest_period, latest_period, profile), 1)
        rate = round(data_periods / expected * 100, 2) if expected else 0.0
        result.append(
            CompletenessItem(
                disease_name=disease_name,
                data_periods=data_periods,
                expected_periods=expected,
                completeness_rate=rate,
                earliest_date=earliest_date.strftime("%Y-%m-%d") if earliest_date else None,
                latest_date=latest_date.strftime("%Y-%m-%d") if latest_date else None,
                total_records=len(times),
                period_unit=profile.period_unit,
            )
        )
    return result
