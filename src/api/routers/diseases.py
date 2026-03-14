"""Diseases router – list, detail, records, comparison."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas.disease import DiseaseListItem, DiseaseOut
from src.api.schemas.disease_record import DiseaseRecordOut, TrendPoint
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease

router = APIRouter()


@router.get("/diseases", response_model=List[DiseaseListItem])
async def list_diseases(
    country_id: int = Query(..., ge=1),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    """Return diseases that have records for a country (excludes D999 total row)."""

    if lang == "zh":
        display = func.coalesce(
            StandardDisease.standard_name_zh, Disease.name_en, Disease.name
        ).label("display_name")
    else:
        display = func.coalesce(Disease.name_en, Disease.name).label("display_name")

    q = (
        select(Disease.name.label("code"), display, Disease.name_en.label("display_name_en"))
        .join(DiseaseRecord, DiseaseRecord.disease_id == Disease.id)
        .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
        .where(DiseaseRecord.country_id == country_id, Disease.name != "D999")
        .group_by(Disease.name, display, Disease.name_en)
        .order_by(display)
    )
    rows = (await db.execute(q)).all()
    return [
        DiseaseListItem(code=r.code, display_name=r.display_name or r.code, display_name_en=r.display_name_en)
        for r in rows
    ]


@router.get("/diseases/{disease_code}", response_model=DiseaseOut)
async def get_disease(disease_code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Disease).where(Disease.name == disease_code))
    disease = result.scalar_one_or_none()
    if not disease:
        raise HTTPException(404, "Disease not found")
    return disease


@router.get("/diseases/{disease_code}/records", response_model=List[DiseaseRecordOut])
async def get_disease_records(
    disease_code: str,
    country_id: int = Query(..., ge=1),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """All records for a specific disease+country, ordered by time."""
    q = (
        select(DiseaseRecord)
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(Disease.name == disease_code, DiseaseRecord.country_id == country_id)
        .order_by(DiseaseRecord.time)
        .limit(limit)
    )
    rows = (await db.execute(q)).scalars().all()
    return rows


@router.get("/analysis/compare", response_model=dict)
async def compare_diseases(
    country_id: int = Query(..., ge=1),
    diseases: str = Query(..., description="Comma-separated disease codes"),
    db: AsyncSession = Depends(get_db),
):
    """Monthly comparison of multiple diseases."""
    codes = [c.strip() for c in diseases.split(",") if c.strip()]
    if not codes or len(codes) > 10:
        raise HTTPException(400, "Provide 1–10 comma-separated disease codes")

    time_period = func.date_trunc("month", DiseaseRecord.time).label("time_period")

    q = (
        select(
            Disease.name.label("disease_code"),
            func.coalesce(Disease.name_en, Disease.name).label("disease_name"),
            time_period,
            func.sum(DiseaseRecord.cases).label("cases"),
            func.sum(DiseaseRecord.deaths).label("deaths"),
        )
        .join(Disease, DiseaseRecord.disease_id == Disease.id)
        .where(DiseaseRecord.country_id == country_id, Disease.name.in_(codes))
        .group_by(Disease.name, Disease.name_en, time_period)
        .order_by(time_period)
    )
    rows = (await db.execute(q)).all()

    # Group by disease code
    result: dict = {}
    for r in rows:
        key = r.disease_code
        if key not in result:
            result[key] = {"disease_code": key, "disease_name": r.disease_name, "data": []}
        result[key]["data"].append(
            {
                "time_period": r.time_period.strftime("%Y-%m-%d") if r.time_period else "",
                "cases": int(r.cases or 0),
                "deaths": int(r.deaths or 0),
            }
        )

    return {"diseases": list(result.values())}
