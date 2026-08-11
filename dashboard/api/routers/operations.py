"""SQL-free delivery routes for operational control-plane resources."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.deps import get_db
from dashboard.api.schemas.control_plane import DataResponse, PaginationMeta, ResponseMeta
from dashboard.api.schemas.operations import (
    ScheduleOut,
    ScheduleRunOut,
    SourceOut,
    SourceRunCreate,
    TaskEventOut,
    TaskReferenceOut,
)
from src.control_plane.operations import (
    ScheduleRunQueryRepository,
    SourceOperationsService,
    SourceQueryRepository,
    TaskOperationsService,
    schedule_application_service,
)

router = APIRouter()


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=getattr(request.state, "request_id", None))


def _pagination_meta(request: Request, page: int, page_size: int, total: int) -> ResponseMeta:
    return ResponseMeta(
        request_id=getattr(request.state, "request_id", None),
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        ),
    )


@router.get("/schedules", response_model=DataResponse[list[ScheduleOut]])
async def list_schedules(request: Request, kind: str | None = Query(default=None)):
    try:
        schedules = await schedule_application_service.list(kind=kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DataResponse(data=[ScheduleOut.model_validate(item) for item in schedules], meta=_meta(request))


@router.get("/sources", response_model=DataResponse[list[SourceOut]])
async def list_sources(request: Request, db: AsyncSession = Depends(get_db)):
    sources = await SourceQueryRepository(db).list()
    return DataResponse(data=[SourceOut.model_validate(item) for item in sources], meta=_meta(request))


@router.get("/sources/{country_code}/runs", response_model=DataResponse[list[ScheduleRunOut]])
async def list_source_runs(
    country_code: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await SourceQueryRepository(db).runs(
        country_code,
        page_size,
        (page - 1) * page_size,
    )
    if result is None:
        raise HTTPException(404, "Source country not found")
    runs, total = result
    return DataResponse(
        data=[ScheduleRunOut.model_validate(run) for run in runs],
        meta=_pagination_meta(request, page, page_size, total),
    )


@router.post(
    "/sources/{country_code}/runs",
    response_model=DataResponse[TaskReferenceOut],
    status_code=202,
)
async def start_source_run(
    country_code: str,
    body: SourceRunCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        task = await SourceOperationsService(db).enqueue_run(country_code, body.model_dump())
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if task is None:
        raise HTTPException(404, "Source country not found")
    return DataResponse(data=TaskReferenceOut.model_validate(task), meta=_meta(request))


@router.get("/schedules/{schedule_id}", response_model=DataResponse[ScheduleOut])
async def get_schedule(schedule_id: str, request: Request):
    try:
        schedule = await schedule_application_service.get(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    return DataResponse(data=ScheduleOut.model_validate(schedule), meta=_meta(request))


@router.get("/schedules/{schedule_id}/runs", response_model=DataResponse[list[ScheduleRunOut]])
async def list_schedule_runs(
    schedule_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        runs, total = await ScheduleRunQueryRepository(db).list(
            schedule_id,
            page_size,
            (page - 1) * page_size,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DataResponse(
        data=[ScheduleRunOut.model_validate(run) for run in runs],
        meta=_pagination_meta(request, page, page_size, total),
    )


@router.post(
    "/schedules/{schedule_id}/runs",
    response_model=DataResponse[TaskReferenceOut],
    status_code=202,
)
async def trigger_schedule(schedule_id: str, request: Request):
    try:
        result = await schedule_application_service.trigger(schedule_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataResponse(data=TaskReferenceOut.model_validate(result), meta=_meta(request))


@router.get("/tasks/{task_uuid}/events", response_model=DataResponse[list[TaskEventOut]])
async def task_events(task_uuid: str, request: Request, db: AsyncSession = Depends(get_db)):
    events = await TaskOperationsService(db).events(task_uuid)
    if events is None:
        raise HTTPException(404, "Task not found")
    return DataResponse(data=[TaskEventOut.model_validate(event) for event in events], meta=_meta(request))


@router.post(
    "/tasks/{task_uuid}/retry",
    response_model=DataResponse[TaskReferenceOut],
    status_code=202,
)
async def retry_task(task_uuid: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        task = await TaskOperationsService(db).retry(task_uuid)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if task is None:
        raise HTTPException(404, "Task not found")
    return DataResponse(data=TaskReferenceOut.model_validate(task), meta=_meta(request))


@router.get("/releases", response_model=DataResponse[list[ScheduleOut]])
async def list_releases(request: Request):
    schedules = await schedule_application_service.list(kind="release")
    return DataResponse(data=[ScheduleOut.model_validate(item) for item in schedules], meta=_meta(request))


@router.post(
    "/releases/{job_id}/runs",
    response_model=DataResponse[TaskReferenceOut],
    status_code=202,
)
async def trigger_release(job_id: str, request: Request):
    try:
        result = await schedule_application_service.trigger(f"release:{job_id}")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataResponse(data=TaskReferenceOut.model_validate(result), meta=_meta(request))


__all__ = ["router"]
