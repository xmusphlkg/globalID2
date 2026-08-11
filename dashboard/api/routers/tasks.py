"""Tasks delivery router."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..enum_utils import parse_enum_csv, parse_enum_member
from ..schemas.task import TaskCreateRequest, TaskDetailOut, TaskOut, WorkerStatusOut
from ..schemas.operations import TaskReferenceOut
from src.core.config import get_config
from src.control_plane.events import control_plane_events
from src.control_plane.operations import TaskOperationsService, TaskQueryRepository
from src.domain.task import TaskPriority, TaskStatus, TaskType

router = APIRouter()


def _worker_concurrency() -> int:
    try:
        return max(1, int(get_config().task_worker.concurrency))
    except Exception:
        return 1


# ---------- REST endpoints ----------


@router.get("/tasks", response_model=List[TaskOut])
async def list_tasks(
    response: Response,
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    task_type: Optional[str] = Query(None, description="Comma-separated types"),
    country_code: Optional[str] = Query(None, min_length=2, max_length=10, description="Filter by country code"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    statuses = parse_enum_csv(TaskStatus, status, "status") if status else None
    task_types = parse_enum_csv(TaskType, task_type, "task_type") if task_type else None
    items, total_count = await TaskQueryRepository(db).list(
        statuses=statuses,
        task_types=task_types,
        country_code=country_code,
        search=search,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    response.headers["X-Total-Count"] = str(total_count)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str((page - 1) * page_size)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Limit, X-Offset"

    return [TaskOut.model_validate(item) for item in items]


@router.get("/tasks/worker-status", response_model=WorkerStatusOut)
async def get_worker_status(db: AsyncSession = Depends(get_db)):
    payload = await TaskQueryRepository(db).worker_status(_worker_concurrency())
    return WorkerStatusOut.model_validate(payload)


@router.get("/tasks/{task_uuid}", response_model=TaskDetailOut)
async def get_task(task_uuid: str, db: AsyncSession = Depends(get_db)):
    task = await TaskQueryRepository(db).detail(task_uuid)
    if task is None:
        raise HTTPException(404, "Task not found")
    return TaskDetailOut.model_validate(task)


@router.post("/tasks", response_model=TaskReferenceOut, status_code=202)
async def create_task(body: TaskCreateRequest, db: AsyncSession = Depends(get_db)):
    task_type = parse_enum_member(TaskType, body.task_type, "task_type")
    priority = parse_enum_member(TaskPriority, body.priority, "priority")
    task = await TaskOperationsService(db).create(
        task_type=task_type,
        task_name=body.task_name,
        country_code=body.country_code,
        priority=priority,
        description=body.description,
        input_data=body.input_data or {},
    )
    if task is None:
        raise HTTPException(404, "Country not found")

    await control_plane_events.publish_task_event(
        {
            "event": "task_created",
            "task_uuid": task.task_uuid,
            "status": str(task.status.value if hasattr(task.status, "value") else task.status),
            "progress": task.progress or 0,
        }
    )
    return TaskReferenceOut(
        task_uuid=task.task_uuid,
        status=str(task.status.value if hasattr(task.status, "value") else task.status),
        resource_id=task.task_uuid,
        href=f"/operations/tasks?task={task.task_uuid}",
    )


@router.post("/tasks/{task_uuid}/cancel", response_model=TaskReferenceOut, status_code=202)
async def cancel_task(task_uuid: str, db: AsyncSession = Depends(get_db)):
    try:
        cancelled = await TaskOperationsService(db).cancel(task_uuid)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not cancelled:
        raise HTTPException(404, "Task not found")
    return TaskReferenceOut(
        task_uuid=cancelled.task_uuid,
        status=str(cancelled.status.value if hasattr(cancelled.status, "value") else cancelled.status),
        resource_id=cancelled.task_uuid,
        href=f"/operations/tasks?task={cancelled.task_uuid}",
    )
