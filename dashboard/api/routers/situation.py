"""Internal review endpoints for the public Situation Room."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.database import get_db
from src.domain import PublicHealthEvent, SituationOverride, SituationSnapshot
from src.services.situation_room import refresh_situation

router = APIRouter(prefix="/situation")


class EventDecision(BaseModel):
    action: Literal["publish", "suppress", "merge", "correct"]
    note: str | None = Field(default=None, max_length=4000)
    actor: str | None = Field(default="dashboard", max_length=160)
    payload: dict = Field(default_factory=dict)


@router.get("/candidates")
async def candidates(status: str = "candidate", limit: int = 100) -> list[dict]:
    async with get_db() as db:
        rows = (await db.execute(
            select(PublicHealthEvent)
            .where(PublicHealthEvent.status == status)
            .order_by(PublicHealthEvent.created_at.desc())
            .limit(min(max(limit, 1), 250))
        )).scalars().all()
    return [row.to_dict() for row in rows]


@router.patch("/events/{event_id}")
async def decide_event(event_id: int, decision: EventDecision) -> dict:
    async with get_db() as db:
        event = await db.get(PublicHealthEvent, event_id)
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
            target_id=str(event_id),
            action=decision.action,
            note=decision.note,
            actor=decision.actor,
            payload=decision.payload,
        ))
        await db.flush()
        return event.to_dict()


@router.post("/rebuild")
async def rebuild_situation(fetch_events: bool = True) -> dict:
    return await refresh_situation(fetch_events=fetch_events)


@router.get("/runs")
async def runs(limit: int = 30) -> list[dict]:
    async with get_db() as db:
        rows = (await db.execute(
            select(SituationSnapshot)
            .order_by(SituationSnapshot.created_at.desc())
            .limit(min(max(limit, 1), 100))
        )).scalars().all()
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
