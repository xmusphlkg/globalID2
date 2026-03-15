"""Crawl router — trigger and monitor crawl tasks from the dashboard."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.task import TaskOut
from src.core.task_manager import task_manager
from src.domain.country import Country
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType

router = APIRouter()


# ── Request schema ────────────────────────────────────────────────────────────

class CrawlStartRequest(BaseModel):
    """Body for POST /crawl/start."""
    country_id: int = Field(..., ge=1, description="Country DB id")
    source: str = Field("all", description="Crawl source: cdc_weekly / nhc / pubmed / all")
    force: bool = Field(False, description="Ignore DB and re-crawl everything")
    process: bool = Field(True, description="Also run data processing after fetch")
    save_raw: bool = Field(True, description="Archive original HTML pages")
    fill_missing: bool = Field(True, description="Backfill missing months")
    priority: str = Field("normal", description="Task priority")


class TaskExecuteRequest(BaseModel):
    """Body for POST /tasks/{uuid}/execute."""
    pass  # no extra params — everything comes from the task's input_data


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/crawl/start", response_model=TaskOut, status_code=201)
async def start_crawl(
    body: CrawlStartRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a crawl task AND immediately start executing it in the background.

    Returns the task object right away (status = pending → will move to running).
    """
    # Resolve country code from country_id
    country = (await db.execute(
        select(Country).where(Country.id == body.country_id)
    )).scalar_one_or_none()
    if not country:
        raise HTTPException(404, f"Country not found: {body.country_id}")
    country_code = country.code.upper()

    # Check for already-running crawl tasks for this country
    running_q = select(Task).where(
        Task.task_type == TaskType.CRAWL_DATA,
        Task.country_id == body.country_id,
        Task.status.in_([TaskStatus.RUNNING, TaskStatus.QUEUED]),
    )
    running = (await db.execute(running_q)).scalar_one_or_none()
    if running:
        raise HTTPException(
            409,
            f"A crawl task is already running for this country (task {running.task_uuid})",
        )

    # Create the task via task_manager (so UUID is generated properly)
    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name=f"Crawl {country_code} Data ({body.source})",
        country_id=body.country_id,
        priority=TaskPriority(body.priority) if body.priority else TaskPriority.NORMAL,
        description=(
            f"Source: {body.source}, Force: {'Yes' if body.force else 'No'}, "
            f"Process: {'Yes' if body.process else 'No'}"
        ),
        input_data={
            "country": country_code,
            "country_code": country_code,
            "source": body.source,
            "force": body.force,
            "process": body.process,
            "save_raw": body.save_raw,
            "fill_missing": body.fill_missing,
        },
    )

    # Schedule background execution
    background_tasks.add_task(_execute_in_background, task.task_uuid)

    return _task_to_out(task)


@router.post("/tasks/{task_uuid}/execute", response_model=TaskOut, status_code=202)
async def execute_existing_task(
    task_uuid: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Execute an existing pending/failed task in the background.
    """
    task = (await db.execute(
        select(Task).where(Task.task_uuid == task_uuid)
    )).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.FAILED):
        raise HTTPException(
            409,
            f"Task status is '{task.status}' — only pending/queued/failed tasks can be executed",
        )

    background_tasks.add_task(_execute_in_background, task.task_uuid)

    return _task_to_out(task)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _execute_in_background(task_uuid: str) -> None:
    """Wrap the task executor; import lazily to avoid circular imports."""
    from src.services.task_executor import execute_task_background
    await execute_task_background(task_uuid)


def _task_to_out(task: Task) -> TaskOut:
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
    )
