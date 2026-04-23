"""Tasks router – list, detail, create, worker status, WebSocket live updates."""

import os
from pathlib import Path
import subprocess
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.task import TaskCreateRequest, TaskDetailOut, TaskOut, WorkbookEntryOut, WorkerStatusOut
from src.core.config import get_config
from src.core.task_manager import task_manager
from src.domain.country import Country
from src.domain.task import Task, TaskStatus, TaskType, TaskWorkbook

router = APIRouter()


def _worker_concurrency() -> int:
    try:
        return max(1, int(get_config().task_worker.concurrency))
    except Exception:
        return 1


def _cancel_meta(task: Task) -> tuple[bool, Optional[str]]:
    metadata = dict(task.metadata_ or {})
    requested_at = metadata.get("cancel_requested_at")
    return bool(metadata.get("cancel_requested")), requested_at if isinstance(requested_at, str) else None


def _worker_pid_path() -> Path:
    return Path(__file__).resolve().parents[3] / "logs" / "dashboard-worker.pid"


def _read_worker_pid() -> Optional[int]:
    pid_path = _worker_pid_path()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except Exception:
        return None


def _is_pid_running(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _find_worker_pid() -> Optional[int]:
    """Fallback discovery for worker processes started outside dashboard.sh."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or "src.services.task_worker" not in line:
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if _is_pid_running(pid):
            return pid
    return None

# ---------- WebSocket hub ----------

class _ConnectionHub:
    """Lightweight in-process WebSocket fanout."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict):
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


task_hub = _ConnectionHub()

# ---------- REST endpoints ----------


@router.get("/tasks", response_model=List[TaskOut])
async def list_tasks(
    response: Response,
    status: Optional[str] = Query(None, description="Comma-separated statuses"),
    task_type: Optional[str] = Query(None, description="Comma-separated types"),
    country_id: Optional[int] = Query(None, ge=1, description="Filter by country id"),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status:
        vals = [s.strip() for s in status.split(",") if s.strip()]
        filters.append(Task.status.in_(vals))
    if task_type:
        vals = [t.strip() for t in task_type.split(",") if t.strip()]
        filters.append(Task.task_type.in_(vals))
    if country_id is not None:
        filters.append(Task.country_id == country_id)
    if search:
        like = f"%{search}%"
        filters.append(
            Task.task_name.ilike(like)
            | Task.task_uuid.ilike(like)
            | Task.description.ilike(like)
            | cast(Task.input_data, Text).ilike(like)
            | cast(Task.output_data, Text).ilike(like)
            | cast(Task.metadata_, Text).ilike(like)
        )

    count_q = select(func.count()).select_from(Task)
    if filters:
        count_q = count_q.where(*filters)
    total_count = int((await db.execute(count_q)).scalar_one() or 0)

    q = (
        select(
            Task,
            Country.code.label("country_code"),
            Country.name_en.label("country_name"),
            func.count(TaskWorkbook.id).label("workbook_count"),
        )
        .outerjoin(Country, Country.id == Task.country_id)
        .outerjoin(TaskWorkbook, TaskWorkbook.task_id == Task.id)
        .group_by(Task.id, Country.code, Country.name_en)
        .order_by(Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if filters:
        q = q.where(*filters)

    rows = (await db.execute(q)).all()

    response.headers["X-Total-Count"] = str(total_count)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Limit, X-Offset"

    return [
        TaskOut(
            id=r.Task.id,
            task_uuid=r.Task.task_uuid,
            task_name=r.Task.task_name,
            task_type=r.Task.task_type,
            status=r.Task.status,
            priority=r.Task.priority,
            progress=r.Task.progress or 0,
            country_id=r.Task.country_id,
            country_code=r.country_code,
            country_name=r.country_name,
            report_id=r.Task.report_id,
            description=r.Task.description,
            last_error=r.Task.last_error,
            created_at=r.Task.created_at,
            started_at=r.Task.started_at,
            completed_at=r.Task.completed_at,
            actual_duration=r.Task.actual_duration,
            workbook_count=r.workbook_count,
            cancel_requested=_cancel_meta(r.Task)[0],
            cancel_requested_at=_cancel_meta(r.Task)[1],
        )
        for r in rows
    ]


@router.get("/tasks/worker-status", response_model=WorkerStatusOut)
async def get_worker_status(db: AsyncSession = Depends(get_db)):
    counts_q = select(
        func.count().filter(Task.status == TaskStatus.QUEUED).label("queued_tasks"),
        func.count().filter(Task.status == TaskStatus.RUNNING).label("running_tasks"),
        func.count().filter(Task.status == TaskStatus.RETRYING).label("retrying_tasks"),
        func.max(Task.created_at).label("latest_created_at"),
        func.max(Task.started_at).label("latest_started_at"),
        func.max(Task.completed_at).label("latest_completed_at"),
    )
    row = (await db.execute(counts_q)).one()

    worker_pid = _read_worker_pid()
    if not _is_pid_running(worker_pid):
        worker_pid = _find_worker_pid()
    worker_process_running = _is_pid_running(worker_pid)
    running = int(row.running_tasks or 0)
    retrying = int(row.retrying_tasks or 0)

    return WorkerStatusOut(
        worker_process_running=worker_process_running,
        worker_pid=worker_pid if worker_process_running else None,
        worker_concurrency=_worker_concurrency(),
        queued_tasks=int(row.queued_tasks or 0),
        running_tasks=running,
        retrying_tasks=retrying,
        active_tasks=running + retrying,
        latest_created_at=row.latest_created_at,
        latest_started_at=row.latest_started_at,
        latest_completed_at=row.latest_completed_at,
    )


@router.get("/tasks/{task_uuid}", response_model=TaskDetailOut)
async def get_task(task_uuid: str, db: AsyncSession = Depends(get_db)):
    q = (
        select(Task, Country.code.label("country_code"), Country.name_en.label("country_name"))
        .outerjoin(Country, Country.id == Task.country_id)
        .where(Task.task_uuid == task_uuid)
    )
    row = (await db.execute(q)).one_or_none()
    task = row.Task if row else None
    if not task:
        raise HTTPException(404, "Task not found")

    wb_q = (
        select(TaskWorkbook)
        .where(TaskWorkbook.task_id == task.id)
        .order_by(TaskWorkbook.created_at)
    )
    wbs = (await db.execute(wb_q)).scalars().all()

    wb_count = len(wbs)

    cancel_requested, cancel_requested_at = _cancel_meta(task)

    return TaskDetailOut(
        id=task.id,
        task_uuid=task.task_uuid,
        task_name=task.task_name,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        progress=task.progress or 0,
        country_id=task.country_id,
        country_code=row.country_code,
        country_name=row.country_name,
        report_id=task.report_id,
        description=task.description,
        last_error=task.last_error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        actual_duration=task.actual_duration,
        workbook_count=wb_count,
        cancel_requested=cancel_requested,
        cancel_requested_at=cancel_requested_at,
        input_data=task.input_data,
        output_data=task.output_data,
        parent_task_id=task.parent_task_id,
        workbook_entries=[
            WorkbookEntryOut(
                id=w.id,
                entry_uuid=w.entry_uuid,
                entry_type=w.entry_type,
                title=w.title,
                content=w.content,
                content_type=w.content_type,
                prompt=w.prompt,
                response=w.response,
                model_used=w.model_used,
                tokens_used=w.tokens_used,
                cost=w.cost,
                duration=w.duration,
                success=w.success,
                error_message=w.error_message,
                metadata=w.metadata_ or {},
                created_at=w.created_at,
            )
            for w in wbs
        ],
    )


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreateRequest, db: AsyncSession = Depends(get_db)):
    task = await task_manager.create_task(
        task_type=body.task_type,
        task_name=body.task_name,
        country_id=body.country_id,
        priority=body.priority,
        description=body.description,
        input_data=body.input_data or {},
    )

    # Broadcast the creation to subscribers
    await task_hub.broadcast(
        {
            "event": "task_created",
            "task_uuid": task.task_uuid,
            "status": task.status,
            "progress": task.progress or 0,
        }
    )
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
        country_code=None,
        country_name=None,
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


@router.post("/tasks/{task_uuid}/cancel", response_model=TaskOut, status_code=202)
async def cancel_task(task_uuid: str, db: AsyncSession = Depends(get_db)):
    task = (await db.execute(select(Task).where(Task.task_uuid == task_uuid))).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(409, f"Task status is '{task.status}' — this task can no longer be cancelled")

    cancelled = await task_manager.request_task_cancel(task_uuid)
    if not cancelled:
        raise HTTPException(404, "Task not found")

    cancel_requested, cancel_requested_at = _cancel_meta(cancelled)
    return TaskOut(
        id=cancelled.id,
        task_uuid=cancelled.task_uuid,
        task_name=cancelled.task_name,
        task_type=cancelled.task_type,
        status=cancelled.status,
        priority=cancelled.priority,
        progress=cancelled.progress or 0,
        country_id=cancelled.country_id,
        country_code=None,
        country_name=None,
        report_id=cancelled.report_id,
        description=cancelled.description,
        last_error=cancelled.last_error,
        created_at=cancelled.created_at,
        started_at=cancelled.started_at,
        completed_at=cancelled.completed_at,
        actual_duration=cancelled.actual_duration,
        workbook_count=0,
        cancel_requested=cancel_requested,
        cancel_requested_at=cancel_requested_at,
    )


# ---------- WebSocket ----------


@router.websocket("/tasks/ws")
async def task_ws(ws: WebSocket):
    await task_hub.connect(ws)
    try:
        while True:
            # Keep the connection alive; client can send pings.
            await ws.receive_text()
    except WebSocketDisconnect:
        task_hub.disconnect(ws)
