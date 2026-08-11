"""Overview router – KPIs, top diseases, trend data.

All queries use parameterised binds to prevent SQL injection.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..frequency import (
    infer_country_frequency,
    infer_frequency_profile_from_times,
    period_start,
)
from ..schemas.disease_record import (
    MonthlyComparisonPoint,
    OverviewSummary,
    TopDiseaseItem,
    TrendPoint,
)
from ..services.disease_series_projection import load_series_first_records
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord
from src.domain.country import Country
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


async def _resolve_country_id(country_code: str, db: AsyncSession) -> int:
    country_id = (
        await db.execute(
            select(Country.id).where(func.upper(Country.code) == country_code.strip().upper())
        )
    ).scalar_one_or_none()
    if country_id is None:
        raise HTTPException(404, "Country not found")
    return int(country_id)


@router.get("/analytics/summary", response_model=OverviewSummary)
async def overview_summary(
    country_code: str = Query(..., min_length=2, max_length=10),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    """Single aggregated call that powers the Overview page KPIs + top diseases."""

    country_id = await _resolve_country_id(country_code, db)

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


@router.get("/analytics/trends", response_model=List[TrendPoint])
async def overview_trend(
    country_code: str = Query(..., min_length=2, max_length=10),
    disease_code: Optional[str] = Query(
        None, description="Disease code, e.g. D001. Omit for total (D999)."
    ),
    interval: Optional[int] = Query(
        None, description="Days to look back. Omit for all time."
    ),
    start_date: Optional[date] = Query(
        None, description="Inclusive start date. Overrides interval when present."
    ),
    end_date: Optional[date] = Query(
        None, description="Inclusive end date. Overrides interval when present."
    ),
    db: AsyncSession = Depends(get_db),
):
    """Monthly trend data for a given country and (optional) disease."""

    country_id = await _resolve_country_id(country_code, db)

    if disease_code:
        projected = await load_series_first_records(
            db,
            disease_code=disease_code,
            country_id=country_id,
        )
        records = _filter_projected_records(
            projected.records,
            start_date=start_date,
            end_date=end_date,
            interval=interval,
        )
        profile = infer_frequency_profile_from_times(
            record["time"]
            for record in records
            if isinstance(record.get("time"), datetime)
        )
        grouped: dict[datetime, list[dict]] = defaultdict(list)
        for record in records:
            record_time = record.get("time")
            if isinstance(record_time, datetime):
                grouped[period_start(record_time, profile)].append(record)
        return [
            TrendPoint(
                time_period=bucket.strftime("%Y-%m-%d"),
                cases=int(sum(float(item.get("cases") or 0) for item in items)),
                deaths=int(sum(float(item.get("deaths") or 0) for item in items)),
                incidence_rate=_average_rate(items, "incidence_rate"),
                mortality_rate=_average_rate(items, "mortality_rate"),
            )
            for bucket, items in sorted(grouped.items())
        ]

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
            incidence_rate=(
                round(float(r.incidence_rate), 4)
                if r.incidence_rate is not None
                else None
            ),
            mortality_rate=(
                round(float(r.mortality_rate), 4)
                if r.mortality_rate is not None
                else None
            ),
        )
        for r in rows
    ]


@router.get("/analytics/monthly-comparison", response_model=List[MonthlyComparisonPoint])
async def overview_monthly_comparison(
    country_code: str = Query(..., min_length=2, max_length=10),
    disease_code: Optional[str] = Query(
        None, description="Disease code, e.g. D001. Omit for total (D999)."
    ),
    interval: Optional[int] = Query(
        None, description="Days to look back. Omit for all time."
    ),
    start_date: Optional[date] = Query(
        None, description="Inclusive start date. Overrides interval when present."
    ),
    end_date: Optional[date] = Query(
        None, description="Inclusive end date. Overrides interval when present."
    ),
    db: AsyncSession = Depends(get_db),
):
    """Year-by-month comparison for seasonality and structural shifts."""

    country_id = await _resolve_country_id(country_code, db)

    if disease_code:
        projected = await load_series_first_records(
            db,
            disease_code=disease_code,
            country_id=country_id,
        )
        records = _filter_projected_records(
            projected.records,
            start_date=start_date,
            end_date=end_date,
            interval=interval,
        )
        grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for record in records:
            record_time = record.get("time")
            if isinstance(record_time, datetime):
                grouped[(record_time.year, record_time.month)].append(record)
        return [
            MonthlyComparisonPoint(
                year=year,
                month=month,
                cases=int(sum(float(item.get("cases") or 0) for item in items)),
                deaths=int(sum(float(item.get("deaths") or 0) for item in items)),
                incidence_rate=_average_rate(items, "incidence_rate"),
                mortality_rate=_average_rate(items, "mortality_rate"),
            )
            for (year, month), items in sorted(grouped.items())
        ]

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
            incidence_rate=(
                round(float(r.incidence_rate), 4)
                if r.incidence_rate is not None
                else None
            ),
            mortality_rate=(
                round(float(r.mortality_rate), 4)
                if r.mortality_rate is not None
                else None
            ),
        )
        for r in rows
    ]


def _filter_projected_records(
    records: list[dict],
    *,
    start_date: date | None,
    end_date: date | None,
    interval: int | None,
) -> list[dict]:
    """Filter an already-safe curve without returning to the flat table."""

    start_boundary = (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        if start_date
        else None
    )
    end_boundary = (
        datetime.combine(end_date, time.max, tzinfo=timezone.utc) if end_date else None
    )
    if interval is not None and start_boundary is None and end_boundary is None:
        start_boundary = datetime.now(timezone.utc) - timedelta(days=interval)

    result: list[dict] = []
    for record in records:
        record_time = record.get("time")
        if not isinstance(record_time, datetime):
            continue
        normalized = (
            record_time.replace(tzinfo=timezone.utc)
            if record_time.tzinfo is None
            else record_time.astimezone(timezone.utc)
        )
        if start_boundary and normalized < start_boundary:
            continue
        if end_boundary and normalized > end_boundary:
            continue
        result.append({**record, "time": normalized})
    return result


def _average_rate(records: list[dict], field: str) -> float | None:
    values = [
        float(record[field])
        for record in records
        if record.get(field) is not None and float(record[field]) >= 0
    ]
    return round(sum(values) / len(values), 4) if values else None
