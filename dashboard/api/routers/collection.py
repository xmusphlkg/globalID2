"""Collection facade. Artifacts are metadata only; no byte/download endpoint."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.control_plane.operations import (
    SourceOperationsService,
    schedule_application_service,
)
from src.domain.collection import LINES, PublishDirty, RunStage, SourceArtifact
from src.domain.country import Country
from src.domain.task import Task, TaskStatus, TaskType

from ..deps import get_db
from ..schemas.collection import (
    ArtifactOut,
    KnowledgeRunCreate,
    LineOut,
    LiteratureRunCreate,
    RunOut,
    SituationRunCreate,
    StageOut,
    SurveillanceRunCreate,
)
from ..schemas.control_plane import DataResponse

DB_DEPENDENCY = Depends(get_db)

router = APIRouter(prefix="/collection")
LINE_TYPES = {
    "surveillance": [TaskType.CRAWL_DATA],
    "literature": [TaskType.SYNC_LITERATURE],
    "situation": [TaskType.REFRESH_SITUATION_SOURCES],
    "knowledge": [
        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
    ],
}


def value(v):
    return v.value if hasattr(v, "value") else v


def public_stage_error(error: str | None, limit: int = 280) -> str | None:
    """Keep the failure reason readable when the stored text continues into JSON."""
    if not error:
        return None
    line = next(
        (part.strip() for part in error.replace("\r", "\n").split("\n") if part.strip()),
        "",
    )
    if not line:
        return None
    brace = line.find("{")
    if brace > 0:
        line = line[:brace].rstrip(" :")
    line = " ".join(line.split())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line or None


async def list_unresolved_failed_stages(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Latest attempt of a stage that is still failed on a task that is still failed.

    Recovered attempts, completed or cancelled tasks, and stage rows with no task
    are omitted. The overview otherwise lists historical noise as current work.
    """
    latest_attempt = (
        select(
            RunStage.task_uuid.label("task_uuid"),
            RunStage.stage.label("stage"),
            func.max(RunStage.attempt).label("attempt"),
        )
        .group_by(RunStage.task_uuid, RunStage.stage)
        .subquery()
    )
    rows = (
        await db.execute(
            select(RunStage, Task.task_name, Task.task_type, Task.status)
            .join(
                latest_attempt,
                and_(
                    RunStage.task_uuid == latest_attempt.c.task_uuid,
                    RunStage.stage == latest_attempt.c.stage,
                    RunStage.attempt == latest_attempt.c.attempt,
                ),
            )
            .join(Task, Task.task_uuid == RunStage.task_uuid)
            .where(RunStage.status == "failed")
            .where(Task.status == TaskStatus.FAILED)
            .order_by(RunStage.finished_at.desc(), RunStage.started_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "task_uuid": stage.task_uuid,
            "task_name": task_name,
            "task_type": value(task_type),
            "task_status": value(task_status),
            "stage": stage.stage,
            "attempt": stage.attempt,
            "error": public_stage_error(stage.error),
            "finished_at": stage.finished_at.isoformat() if stage.finished_at else None,
        }
        for stage, task_name, task_type, task_status in rows
    ]


def task_line(task):
    return next(
        (line for line, types in LINE_TYPES.items() if task.task_type in types), None
    )


@router.get("/lines", response_model=DataResponse[list[LineOut]])
async def lines(db: AsyncSession = DB_DEPENDENCY):
    from ..schemas.operations import ScheduleOut

    schedules = [
        ScheduleOut.model_validate(item).model_dump()
        for item in await schedule_application_service.list()
    ]
    result = []
    for line in LINES:
        kind = {
            "surveillance": "ingestion",
            "literature": "literature",
            "situation": "situation",
        }.get(line)
        related = [item for item in schedules if item["kind"] == kind]
        task = (
            await db.execute(
                select(Task)
                .where(Task.task_type.in_(LINE_TYPES[line]))
                .order_by(Task.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        stage = None
        if task:
            stage = (
                await db.execute(
                    select(RunStage)
                    .where(RunStage.task_uuid == task.task_uuid)
                    .order_by(RunStage.attempt.desc(), RunStage.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        pending = (
            await db.execute(
                select(func.count())
                .select_from(PublishDirty)
                .where(PublishDirty.line == line, PublishDirty.published_at.is_(None))
            )
        ).scalar_one()
        dates = [item["next_run_at"] for item in related if item.get("next_run_at")]
        result.append(
            LineOut(
                line=line,
                schedule_ids=[item["id"] for item in related],
                next_run_at=min(dates) if dates else None,
                latest_task_uuid=task.task_uuid if task else None,
                latest_stage=stage.stage if stage else None,
                latest_stage_status=stage.status if stage else None,
                pending_publish_count=pending,
            )
        )
    return DataResponse(data=result)


@router.get("/summary", response_model=DataResponse[dict])
async def summary(db: AsyncSession = DB_DEPENDENCY):
    from src.domain.ai_work_item import AIWorkItem

    failed = await list_unresolved_failed_stages(db)
    queue = dict(
        (
            await db.execute(
                select(AIWorkItem.status, func.count()).group_by(AIWorkItem.status)
            )
        ).all()
    )
    pending = dict(
        (
            await db.execute(
                select(PublishDirty.line, func.count())
                .where(PublishDirty.published_at.is_(None))
                .group_by(PublishDirty.line)
            )
        ).all()
    )
    schedules = await schedule_application_service.list()
    from datetime import datetime, timezone

    from ..schemas.operations import ScheduleOut

    normalized = [ScheduleOut.model_validate(item).model_dump() for item in schedules]
    now = datetime.now(timezone.utc)
    missed = [
        item
        for item in normalized
        if item["kind"] != "release"
        and item["enabled"]
        and item["next_run_at"]
        and item["next_run_at"].replace(
            tzinfo=item["next_run_at"].tzinfo or timezone.utc
        )
        < now
    ]
    return DataResponse(
        data={
            "failed_stages": failed,
            "model_queue": {
                "queued": queue.get("queued", 0),
                "leased": queue.get("leased", 0),
            },
            "pending_publish": pending,
            "overdue_schedules": missed,
        }
    )


@router.get("/runs/{task_uuid}", response_model=DataResponse[RunOut])
async def run(task_uuid: str, db: AsyncSession = DB_DEPENDENCY):
    task = (
        await db.execute(select(Task).where(Task.task_uuid == task_uuid))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(404, "Task not found")
    stages = (
        (
            await db.execute(
                select(RunStage)
                .where(RunStage.task_uuid == task_uuid)
                .order_by(RunStage.attempt, RunStage.id)
            )
        )
        .scalars()
        .all()
    )
    artifacts = (
        (
            await db.execute(
                select(SourceArtifact)
                .where(SourceArtifact.task_uuid == task_uuid)
                .order_by(SourceArtifact.id)
            )
        )
        .scalars()
        .all()
    )
    dirty = (
        (
            await db.execute(
                select(PublishDirty)
                .where(PublishDirty.source_task_uuid == task_uuid)
                .order_by(PublishDirty.id)
            )
        )
        .scalars()
        .all()
    )
    country = await db.get(Country, task.country_id) if task.country_id else None
    fields = dict(task.input_data or {})
    return DataResponse(
        data=RunOut(
            task_uuid=task_uuid,
            task_type=value(task.task_type),
            line=task_line(task),
            status=value(task.status),
            attempt=max((s.attempt for s in stages), default=0),
            country_code=country.code if country else None,
            source=fields.get("source"),
            stages=[StageOut.model_validate(s, from_attributes=True) for s in stages],
            artifacts=[
                ArtifactOut.model_validate(a, from_attributes=True) for a in artifacts
            ],
            publication=[
                {
                    "line": d.line,
                    "scope_key": d.scope_key,
                    "published_at": d.published_at,
                    "published_task_uuid": d.published_task_uuid,
                }
                for d in dirty
            ],
        )
    )


@router.post("/runs", response_model=DataResponse[dict], status_code=202)
async def start_run(
    body: Annotated[
        SurveillanceRunCreate
        | LiteratureRunCreate
        | SituationRunCreate
        | KnowledgeRunCreate,
        Body(discriminator="line"),
    ],
    request: Request,
    db: AsyncSession = DB_DEPENDENCY,
):
    if body.line == "surveillance":
        try:
            result = await SourceOperationsService(db).enqueue_run(
                body.country_code, body.model_dump(exclude={"line", "country_code"})
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "Source country not found")
        return DataResponse(data=result)
    if body.line == "literature":
        from .literature import sync

        response = await sync(body, request)
        return DataResponse(data=response.data.model_dump(mode="json"))
    if body.line == "situation":
        from .situation_notices import queue_refresh

        return DataResponse(data=await queue_refresh(body))
    from .ai import start_disease_knowledge_tasks

    result = await start_disease_knowledge_tasks(body, db)
    return DataResponse(
        data=result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    )
