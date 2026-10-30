"""Publication reads committed scopes and enqueues releases; it never collects."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.collection import PublishDirty
from src.services.data_release_service import data_release_service

from ..deps import get_db
from ..schemas.collection import PendingOut, PublishRunCreate
from ..schemas.control_plane import DataResponse

DB_DEPENDENCY = Depends(get_db)

router = APIRouter(prefix="/publish")


@router.get("/pending", response_model=DataResponse[PendingOut])
async def pending(db: AsyncSession = DB_DEPENDENCY):
    rows = (
        await db.execute(
            select(PublishDirty.line, PublishDirty.scope_key)
            .where(PublishDirty.published_at.is_(None))
            .distinct()
            .order_by(PublishDirty.line, PublishDirty.scope_key)
        )
    ).all()
    groups = {
        line: [scope for item, scope in rows if item == line]
        for line in ("surveillance", "literature", "situation", "knowledge")
    }
    return DataResponse(
        data=PendingOut(
            surveillance=groups["surveillance"],
            literature=bool(groups["literature"]),
            situation=bool(groups["situation"]),
            knowledge=groups["knowledge"],
        )
    )


@router.post("/runs", response_model=DataResponse[dict], status_code=202)
async def start(body: PublishRunCreate):
    try:
        result = await data_release_service.trigger_job(
            body.job_id, manual=True, trigger="dashboard", full=body.full
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataResponse(data=result)
