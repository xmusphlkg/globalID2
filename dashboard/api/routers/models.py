"""Public model capacity and work queue, without provider credentials."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.model_center import get_runtime_routes
from src.domain.ai_work_item import AIWorkItem

from ..deps import get_db
from ..schemas.control_plane import DataResponse

DB_DEPENDENCY = Depends(get_db)

router = APIRouter(prefix="/models")


class PriorityChange(BaseModel):
    priority: Literal["urgent", "high", "normal", "low"]


@router.get("/routes", response_model=DataResponse[list[dict]])
async def routes(db: AsyncSession = DB_DEPENDENCY):
    active = dict(
        (
            await db.execute(
                select(AIWorkItem.route_key, func.count())
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at > datetime.now(timezone.utc),
                )
                .group_by(AIWorkItem.route_key)
            )
        ).all()
    )
    fields = (
        "model_key",
        "available_for_routing",
        "runtime_model_capacity",
        "rate_limit_cooldown_until",
        "runtime_failure_cooldown_until",
        "failure_cooldown_until",
        "routing_status_reason",
    )
    return DataResponse(
        data=[
            {
                **{key: route.get(key) for key in fields},
                "in_flight": active.get(route["model_key"], 0),
            }
            for route in await get_runtime_routes()
        ]
    )


@router.get("/queue", response_model=DataResponse[list[dict]])
async def queue(
    status: Literal["queued", "leased", "succeeded", "failed", "cancelled"] = "queued",
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = DB_DEPENDENCY,
):
    from sqlalchemy import case

    order = case(
        {"urgent": 0, "high": 1, "normal": 2, "low": 3},
        value=AIWorkItem.priority,
        else_=4,
    )
    rows = (
        (
            await db.execute(
                select(AIWorkItem)
                .where(AIWorkItem.status == status)
                .order_by(order, AIWorkItem.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return DataResponse(data=[project(row) for row in rows])


def project(row, detail=False):
    fields = [
        "id",
        "parent_task_uuid",
        "line",
        "priority",
        "status",
        "available_at",
        "attempt",
        "route_key",
        "lease_expires_at",
        "error",
    ]
    if detail:
        fields += ["payload", "result"]
    return {key: getattr(row, key) for key in fields}


@router.get("/queue/{item_id}", response_model=DataResponse[dict])
async def detail(item_id: int, db: AsyncSession = DB_DEPENDENCY):
    row = await db.get(AIWorkItem, item_id)
    if row is None:
        raise HTTPException(404, "Work item not found")
    return DataResponse(data=project(row, True))


@router.post("/queue/{item_id}/priority", response_model=DataResponse[dict])
async def priority(
    item_id: int, body: PriorityChange, db: AsyncSession = DB_DEPENDENCY
):
    row = (
        await db.execute(
            select(AIWorkItem).where(AIWorkItem.id == item_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Work item not found")
    if row.status != "queued":
        raise HTTPException(409, "Only queued work items can change priority")
    row.priority = body.priority
    await db.commit()
    return DataResponse(data=project(row))
