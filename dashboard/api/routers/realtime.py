"""Realtime JSON observability and controls, under dashboard authentication."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import TaskStatus
from src.services.data_release.realtime_control import RealtimeControlError, realtime_control_service
from ..deps import get_db
from ..enum_utils import parse_enum_csv
from ..schemas.control_plane import DataResponse, PaginationMeta, ResponseMeta
from ..schemas.realtime import (
    RealtimeActionCreate, RealtimeActionOut, RealtimeChannel, RealtimeControlOut,
    RealtimeControlPatch, RealtimeOverviewOut, RealtimePendingPageOut, RealtimeProblemOut, RealtimeRunPageOut,
)

router = APIRouter(prefix="/publish/realtime")


def _meta(request, page=None):
    return ResponseMeta(request_id=getattr(request.state, "request_id", None),
        pagination=PaginationMeta(**{key: page[key] for key in ("page", "page_size", "total", "total_pages")}) if page else None)


def _problem(error: RealtimeControlError, request):
    return JSONResponse(status_code=error.status_code, media_type="application/problem+json", content={
        "type": f"https://globalid.dev/problems/{error.code}", "title": "Realtime publication action unavailable",
        "status": error.status_code, "detail": error.code, "code": error.code,
        "instance": request.url.path, "request_id": getattr(request.state, "request_id", None),
        "task_uuid": error.task_uuid,
        "href": error.href})


@router.get("/overview", response_model=DataResponse[RealtimeOverviewOut])
async def overview(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return DataResponse(data=await realtime_control_service.overview(db), meta=_meta(request))


@router.get("/{channel}/pending", response_model=DataResponse[RealtimePendingPageOut])
async def pending(channel: RealtimeChannel, request: Request, response: Response,
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  eligibility: Literal["all", "publishable", "blocked"] = "all", db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = await realtime_control_service.pending(db, channel, page=page, page_size=page_size, eligibility=eligibility)
    return DataResponse(data=result, meta=_meta(request, result))


@router.get("/{channel}/runs", response_model=DataResponse[RealtimeRunPageOut])
async def runs(channel: RealtimeChannel, request: Request, response: Response,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               status: str | None = None, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    statuses = parse_enum_csv(TaskStatus, status, "status") if status else None
    result = await realtime_control_service.runs(db, channel, page=page, page_size=page_size, statuses=statuses)
    return DataResponse(data=result, meta=_meta(request, result))


@router.post("/{channel}/runs", response_model=DataResponse[RealtimeActionOut], status_code=202,
             responses={409: {"model": RealtimeProblemOut}})
async def start(channel: RealtimeChannel, body: RealtimeActionCreate, request: Request):
    try:
        result = await realtime_control_service.enqueue(channel, job_id=body.job_id)
    except RealtimeControlError as error:
        return _problem(error, request)
    return DataResponse(data=result, meta=_meta(request))


@router.post("/{channel}/retry", response_model=DataResponse[RealtimeActionOut], status_code=202,
             responses={409: {"model": RealtimeProblemOut}})
async def retry(channel: RealtimeChannel, body: RealtimeActionCreate, request: Request):
    try:
        result = await realtime_control_service.enqueue(channel, job_id=body.job_id, retry=True)
    except RealtimeControlError as error:
        return _problem(error, request)
    return DataResponse(data=result, meta=_meta(request))


@router.patch("/{channel}/control", response_model=DataResponse[RealtimeControlOut])
async def control(channel: RealtimeChannel, body: RealtimeControlPatch, request: Request):
    try:
        result = await realtime_control_service.control(channel, paused=body.paused)
    except RealtimeControlError as error:
        return _problem(error, request)
    return DataResponse(data=result, meta=_meta(request))
