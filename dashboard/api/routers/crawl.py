"""Crawl router — trigger and monitor crawl tasks from the dashboard."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.task import TaskOut
from src.core.task_manager import task_manager
from src.domain.country import Country
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType
from src.services.crawl_task_service import crawl_task_service

router = APIRouter()


def _cancel_meta(task: Task) -> tuple[bool, Optional[str]]:
    metadata = dict(task.metadata_ or {})
    requested_at = metadata.get("cancel_requested_at")
    return bool(metadata.get("cancel_requested")), requested_at if isinstance(requested_at, str) else None


# ── Request schema ────────────────────────────────────────────────────────────

class CrawlStartRequest(BaseModel):
    """Body for POST /crawl/start."""
    country_id: int = Field(..., ge=1, description="Country DB id")
    source: str = Field(
        "all",
        description=(
            "Crawl source: nndss_api / cdc_weekly / nhc / pubmed / "
            "jp_weekly / nidss_open_data / sinan_datasus / kdca_open_api "
            "(KR OpenAPI or portal/KOSIS download) / all"
        ),
    )
    force: bool = Field(False, description="Ignore DB and re-crawl everything")
    process: bool = Field(True, description="Also run data processing after fetch")
    save_raw: bool = Field(True, description="Archive original fetched payloads / raw source artifacts")
    fill_missing: bool = Field(False, description="Backfill missing months")
    start_year: Optional[int] = Field(
        None,
        ge=1900,
        le=2100,
        description="Optional historical backfill start year for sources that support it",
    )
    source_file: Optional[str] = Field(
        None,
        description="Optional local KDCA/KOSIS export file path for manual KR imports",
    )
    source_dir: Optional[str] = Field(
        None,
        description="Optional local directory containing KDCA/KOSIS export files",
    )
    priority: str = Field("normal", description="Task priority")


class TaskExecuteRequest(BaseModel):
    """Body for POST /tasks/{uuid}/execute."""
    pass  # no extra params — everything comes from the task's input_data


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/crawl/start", response_model=TaskOut, status_code=201)
async def start_crawl(
    body: CrawlStartRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a crawl task and enqueue it for external worker execution.

    Returns quickly with task status set to queued.
    """
    country = (await db.execute(select(Country).where(Country.id == body.country_id))).scalar_one_or_none()
    if not country:
        raise HTTPException(404, f"Country not found: {body.country_id}")

    try:
        result = await crawl_task_service.enqueue_crawl_task(
            country_id=body.country_id,
            source=body.source,
            force=body.force,
            process=body.process,
            save_raw=body.save_raw,
            fill_missing=body.fill_missing,
            priority=body.priority,
            metadata={
                **({"start_year": body.start_year} if body.start_year is not None else {}),
                **({"source_file": body.source_file} if body.source_file else {}),
                **({"source_dir": body.source_dir} if body.source_dir else {}),
            }
            or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not result.created:
        raise HTTPException(
            409,
            f"A crawl task is already running for this country (task {result.task.task_uuid})",
        )

    return _task_to_out(result.task)


@router.post("/tasks/{task_uuid}/execute", response_model=TaskOut, status_code=202)
async def execute_existing_task(
    task_uuid: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Queue an existing task for external worker execution.
    """
    task = (await db.execute(
        select(Task).where(Task.task_uuid == task_uuid)
    )).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(
            409,
            f"Task status is '{task.status}' — only pending/queued/failed/cancelled tasks can be executed",
        )

    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task

    return _task_to_out(task)


def _task_to_out(task: Task) -> TaskOut:
    cancel_requested, cancel_requested_at = _cancel_meta(task)
    return TaskOut(
        id=task.id,
        task_uuid=task.task_uuid,
        task_name=task.task_name,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        progress=task.progress or 0,
        country_id=task.country_id,
        report_id=task.report_id,
        description=task.description,
        last_error=task.last_error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        actual_duration=task.actual_duration,
        workbook_count=0,
        cancel_requested=cancel_requested,
        cancel_requested_at=cancel_requested_at,
    )
