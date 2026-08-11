"""Internal review endpoints for the public Situation Room."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.core.database import get_db
from src.domain import PublicHealthEvent, SituationOverride, SituationSnapshot
from src.services.situation_room import refresh_situation

router = APIRouter()


class EventDecision(BaseModel):
    action: Literal["publish", "suppress", "merge", "correct"]
    note: str | None = Field(default=None, max_length=4000)
    actor: str | None = Field(default="dashboard", max_length=160)
    payload: dict = Field(default_factory=dict)


@router.get("/overview/events")
async def candidates(
    response: Response,
    status: str = "candidate",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
) -> list[dict]:
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(PublicHealthEvent).where(
                        PublicHealthEvent.status == status
                    )
                )
            ).scalar_one()
            or 0
        )
        offset = (page - 1) * page_size
        rows = (await db.execute(
            select(PublicHealthEvent)
            .where(PublicHealthEvent.status == status)
            .order_by(PublicHealthEvent.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [
        {
            **{key: value for key, value in row.to_dict().items() if key != "id"},
            "id": row.content_hash,
        }
        for row in rows
    ]


@router.patch("/overview/events/{event_key}")
async def decide_event(event_key: str, decision: EventDecision) -> dict:
    async with get_db() as db:
        event = (
            await db.execute(
                select(PublicHealthEvent).where(PublicHealthEvent.content_hash == event_key)
            )
        ).scalar_one_or_none()
        if event is None:
            raise HTTPException(404, "Situation Room event not found")
        if decision.action == "publish":
            event.status = "published"
        elif decision.action == "suppress":
            event.status = "suppressed"
        elif decision.action == "merge":
            event.status = "merged"
        else:
            event.status = "candidate"
        event.review_note = decision.note
        db.add(SituationOverride(
            target_type="event",
            target_id=event_key,
            action=decision.action,
            note=decision.note,
            actor=decision.actor,
            payload=decision.payload,
        ))
        await db.flush()
        return {
            **{key: value for key, value in event.to_dict().items() if key != "id"},
            "id": event.content_hash,
        }


@router.post("/overview/events/rebuild", status_code=202)
async def rebuild_situation(fetch_events: bool = True) -> dict:
    return await refresh_situation(fetch_events=fetch_events)


@router.get("/overview/events/snapshots")
async def runs(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> list[dict]:
    async with get_db() as db:
        total = int(
            (await db.execute(select(func.count()).select_from(SituationSnapshot))).scalar_one()
            or 0
        )
        offset = (page - 1) * page_size
        rows = (await db.execute(
            select(SituationSnapshot)
            .order_by(SituationSnapshot.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [{
        "snapshot_id": row.snapshot_id,
        "snapshot_kind": row.snapshot_kind,
        "iso_week": row.iso_week,
        "generated_at": row.generated_at,
        "data_through": row.data_through,
        "method_version": row.method_version,
        "input_hash": row.input_hash,
        "status": row.status,
        "revision": row.revision,
    } for row in rows]
