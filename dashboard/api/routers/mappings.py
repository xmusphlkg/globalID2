"""Control-plane API for Disease Mapping Registry v3."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.deps import get_db
from src.domain import (
    DiseaseMappingCandidate,
    DiseaseMappingRelease,
    MappingNotificationOutbox,
    SourceDiseaseCategory,
)
from src.services.disease_mapping_ai_service import disease_mapping_ai_service
from src.services.disease_mapping_audit_service import disease_mapping_audit_service
from src.services.disease_mapping_automation_service import disease_mapping_automation_service
from src.services.disease_mapping_registry_service import disease_mapping_registry_service

router = APIRouter()


def _row(instance: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for attribute in sa_inspect(instance).mapper.column_attrs:
        value = getattr(instance, attribute.key)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        output["metadata" if attribute.key == "metadata_" else attribute.key] = value
    return output


class CandidateReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=160)
    notes: str = Field("", max_length=4000)


class ReleaseCreateRequest(BaseModel):
    release_code: str = Field(..., min_length=3, max_length=160)
    created_by: str = Field(..., min_length=1, max_length=160)
    description: str = Field("", max_length=8000)


@router.get("/mappings/summary")
async def mapping_v3_summary(db: AsyncSession = Depends(get_db)):
    return {
        **(await disease_mapping_registry_service.stats(db)),
        "automation": disease_mapping_automation_service.snapshot(),
    }


@router.get("/mappings/categories")
async def list_mapping_v3_categories(
    response: Response,
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    status: Optional[str] = None,
    ai_status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    result = await disease_mapping_registry_service.list_categories(
        db,
        country_code=country_code,
        status=status,
        ai_status=ai_status,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    count_query = select(func.count()).select_from(SourceDiseaseCategory)
    if country_code:
        count_query = count_query.where(SourceDiseaseCategory.country_code == country_code)
    if status:
        count_query = count_query.where(SourceDiseaseCategory.status == status)
    if ai_status:
        count_query = count_query.where(SourceDiseaseCategory.ai_status == ai_status)
    total = int((await db.execute(count_query)).scalar_one() or 0)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str((page - 1) * page_size)
    return result


@router.get("/mappings/coverage")
async def mapping_v3_coverage(db: AsyncSession = Depends(get_db)):
    return await disease_mapping_registry_service.effective_coverage(db)


@router.get("/mappings/audit")
async def mapping_v3_audit(db: AsyncSession = Depends(get_db)):
    return await disease_mapping_audit_service.run(db)


@router.post("/mappings/bootstrap", status_code=202)
async def bootstrap_mapping_v3(db: AsyncSession = Depends(get_db)):
    result = await disease_mapping_registry_service.bootstrap_all_sources(db)
    await db.commit()
    return result


@router.post("/mappings/categories/{category_key}/suggest", status_code=202)
async def suggest_mapping_v3(category_key: str, db: AsyncSession = Depends(get_db)):
    category_id = (
        await db.execute(
            select(SourceDiseaseCategory.id).where(SourceDiseaseCategory.category_key == category_key)
        )
    ).scalar_one_or_none()
    if category_id is None:
        raise HTTPException(404, "Mapping category not found")
    try:
        return await disease_mapping_ai_service.suggest_for_category(db, category_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"AI mapping suggestion failed: {exc}") from exc


@router.post("/mappings/candidates/{candidate_key}/accept")
async def accept_mapping_v3_candidate(
    candidate_key: str,
    body: CandidateReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    candidate_id = (
        await db.execute(
            select(DiseaseMappingCandidate.id).where(
                DiseaseMappingCandidate.candidate_key == candidate_key
            )
        )
    ).scalar_one_or_none()
    if candidate_id is None:
        raise HTTPException(404, "Mapping candidate not found")
    try:
        assertion = await disease_mapping_registry_service.accept_candidate(
            db, candidate_id=candidate_id, reviewer=body.reviewer, notes=body.notes
        )
        await db.commit()
        return _row(assertion)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/mappings/candidates/{candidate_key}/reject")
async def reject_mapping_v3_candidate(
    candidate_key: str,
    body: CandidateReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    candidate_id = (
        await db.execute(
            select(DiseaseMappingCandidate.id).where(
                DiseaseMappingCandidate.candidate_key == candidate_key
            )
        )
    ).scalar_one_or_none()
    if candidate_id is None:
        raise HTTPException(404, "Mapping candidate not found")
    try:
        candidate = await disease_mapping_registry_service.reject_candidate(
            db, candidate_id=candidate_id, reviewer=body.reviewer, notes=body.notes
        )
        await db.commit()
        return _row(candidate)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/mappings/releases", status_code=201)
async def create_mapping_v3_release(
    body: ReleaseCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        release = await disease_mapping_registry_service.create_release(
            db,
            release_code=body.release_code,
            created_by=body.created_by,
            description=body.description,
        )
        await db.commit()
        return _row(release)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.get("/mappings/releases")
async def list_mapping_v3_releases(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await disease_mapping_registry_service.ensure_schema(db)
    total = int(
        (await db.execute(select(func.count()).select_from(DiseaseMappingRelease))).scalar_one()
        or 0
    )
    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            select(DiseaseMappingRelease)
            .order_by(DiseaseMappingRelease.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [_row(item) for item in rows]


@router.post("/mappings/releases/{release_code}/activate")
async def activate_mapping_v3_release(release_code: str, db: AsyncSession = Depends(get_db)):
    release_id = (
        await db.execute(
            select(DiseaseMappingRelease.id).where(
                DiseaseMappingRelease.release_code == release_code
            )
        )
    ).scalar_one_or_none()
    if release_id is None:
        raise HTTPException(404, "Mapping release not found")
    try:
        release = await disease_mapping_registry_service.activate_release(db, release_id)
        await db.commit()
        return _row(release)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/mappings/automation/runs", status_code=202)
async def run_mapping_v3_automation():
    return await disease_mapping_automation_service.process_once()


@router.get("/mappings/outbox")
async def list_mapping_v3_outbox(
    response: Response,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    await disease_mapping_registry_service.ensure_schema(db)
    query = select(MappingNotificationOutbox)
    count_query = select(func.count()).select_from(MappingNotificationOutbox)
    if status:
        query = query.where(MappingNotificationOutbox.status == status)
        count_query = count_query.where(MappingNotificationOutbox.status == status)
    total = int((await db.execute(count_query)).scalar_one() or 0)
    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            query.order_by(MappingNotificationOutbox.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [_row(item) for item in rows]
