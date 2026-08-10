"""Control-plane API for Disease Mapping Registry v3."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.deps import get_db
from src.domain import DiseaseMappingRelease, MappingNotificationOutbox
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


@router.get("/disease-mappings/v3/summary")
async def mapping_v3_summary(db: AsyncSession = Depends(get_db)):
    return {
        **(await disease_mapping_registry_service.stats(db)),
        "automation": disease_mapping_automation_service.snapshot(),
    }


@router.get("/disease-mappings/v3/categories")
async def list_mapping_v3_categories(
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    status: Optional[str] = None,
    ai_status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await disease_mapping_registry_service.list_categories(
        db,
        country_code=country_code,
        status=status,
        ai_status=ai_status,
        limit=limit,
        offset=offset,
    )


@router.get("/disease-mappings/v3/coverage")
async def mapping_v3_coverage(db: AsyncSession = Depends(get_db)):
    return await disease_mapping_registry_service.effective_coverage(db)


@router.get("/disease-mappings/v3/audit")
async def mapping_v3_audit(db: AsyncSession = Depends(get_db)):
    return await disease_mapping_audit_service.run(db)


@router.post("/disease-mappings/v3/bootstrap")
async def bootstrap_mapping_v3(db: AsyncSession = Depends(get_db)):
    result = await disease_mapping_registry_service.bootstrap_all_sources(db)
    await db.commit()
    return result


@router.post("/disease-mappings/v3/categories/{category_id}/suggest")
async def suggest_mapping_v3(category_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await disease_mapping_ai_service.suggest_for_category(db, category_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"AI mapping suggestion failed: {exc}") from exc


@router.post("/disease-mappings/v3/candidates/{candidate_id}/accept")
async def accept_mapping_v3_candidate(
    candidate_id: int,
    body: CandidateReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        assertion = await disease_mapping_registry_service.accept_candidate(
            db, candidate_id=candidate_id, reviewer=body.reviewer, notes=body.notes
        )
        await db.commit()
        return _row(assertion)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/disease-mappings/v3/candidates/{candidate_id}/reject")
async def reject_mapping_v3_candidate(
    candidate_id: int,
    body: CandidateReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        candidate = await disease_mapping_registry_service.reject_candidate(
            db, candidate_id=candidate_id, reviewer=body.reviewer, notes=body.notes
        )
        await db.commit()
        return _row(candidate)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/disease-mappings/v3/releases")
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


@router.get("/disease-mappings/v3/releases")
async def list_mapping_v3_releases(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await disease_mapping_registry_service.ensure_schema(db)
    rows = (
        await db.execute(
            select(DiseaseMappingRelease)
            .order_by(DiseaseMappingRelease.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_row(item) for item in rows]


@router.post("/disease-mappings/v3/releases/{release_id}/activate")
async def activate_mapping_v3_release(release_id: int, db: AsyncSession = Depends(get_db)):
    try:
        release = await disease_mapping_registry_service.activate_release(db, release_id)
        await db.commit()
        return _row(release)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/disease-mappings/v3/automation/run")
async def run_mapping_v3_automation():
    return await disease_mapping_automation_service.process_once()


@router.get("/disease-mappings/v3/outbox")
async def list_mapping_v3_outbox(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    await disease_mapping_registry_service.ensure_schema(db)
    query = select(MappingNotificationOutbox)
    if status:
        query = query.where(MappingNotificationOutbox.status == status)
    rows = (
        await db.execute(query.order_by(MappingNotificationOutbox.created_at.desc()).limit(limit))
    ).scalars().all()
    return [_row(item) for item in rows]
