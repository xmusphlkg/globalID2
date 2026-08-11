"""Thin delivery routes for the control-center overview and runtime."""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.deps import get_db
from dashboard.api.schemas.control_plane import (
    ActionItemOut,
    ControlPlaneOverviewOut,
    DataResponse,
    PaginationMeta,
    ResponseMeta,
    RuntimeSummaryOut,
)
from src.control_plane.events import control_plane_events
from src.control_plane.overview import ControlPlaneOverviewService
from src.control_plane.runtime import runtime_registry

router = APIRouter()


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/overview", response_model=DataResponse[ControlPlaneOverviewOut])
async def overview(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await ControlPlaneOverviewService(db).overview()
    return DataResponse(data=ControlPlaneOverviewOut.model_validate(payload), meta=ResponseMeta(request_id=_request_id(request)))


@router.get("/action-items", response_model=DataResponse[list[ActionItemOut]])
async def action_items(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    payload = await ControlPlaneOverviewService(db).action_items(limit=page * page_size)
    total = len(payload)
    offset = (page - 1) * page_size
    return DataResponse(
        data=[ActionItemOut.model_validate(item) for item in payload[offset : offset + page_size]],
        meta=ResponseMeta(
            request_id=_request_id(request),
            pagination=PaginationMeta(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=math.ceil(total / page_size) if total else 0,
            ),
        ),
    )


@router.get("/runtime/services", response_model=DataResponse[RuntimeSummaryOut])
async def runtime_services(request: Request):
    services, available = await runtime_registry.list_services()
    return DataResponse(
        data=RuntimeSummaryOut(heartbeat_available=available, services=services),
        meta=ResponseMeta(request_id=_request_id(request)),
    )


@router.get(
    "/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def event_stream(request: Request):
    last_event_id = request.headers.get("last-event-id")

    async def generate():
        async for event in control_plane_events.subscribe(last_event_id):
            if await request.is_disconnected():
                break
            event_id = str(event.get("stream_id") or event.get("event_id") or "")
            event_type = str(event.get("type") or "message")
            if event_type == "heartbeat" and not event_id:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps(event, ensure_ascii=False, default=str)
            id_line = f"id: {event_id}\n" if event_id else ""
            yield f"{id_line}event: {event_type}\ndata: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
