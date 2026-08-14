"""Application services and query repositories for operations workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.control_plane.events import control_plane_events
from src.control_plane.runtime import runtime_registry
from src.core.source_scopes import canonicalize_task_source
from src.core.task_manager import task_manager
from src.domain import Country, Task, TaskStatus, TaskType, TaskWorkbook
from src.services.automation_service import automation_service
from src.services.crawl_task_service import crawl_task_service
from src.services.data_release_service import data_release_service
from src.services.literature_service import literature_service


def _value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _cancel_metadata(task: Task) -> tuple[bool, str | None]:
    metadata = dict(task.metadata_ or {})
    requested_at = metadata.get("cancel_requested_at")
    return bool(metadata.get("cancel_requested")), requested_at if isinstance(requested_at, str) else None


def _task_projection(
    task: Task,
    *,
    country_code: str | None = None,
    country_name: str | None = None,
    workbook_count: int = 0,
) -> dict[str, Any]:
    cancel_requested, cancel_requested_at = _cancel_metadata(task)
    return {
        "id": task.id,
        "task_uuid": task.task_uuid,
        "task_name": task.task_name,
        "task_type": task.task_type,
        "status": task.status,
        "priority": task.priority,
        "progress": task.progress or 0,
        "country_id": task.country_id,
        "country_code": country_code,
        "country_name": country_name,
        "report_id": task.report_id,
        "description": task.description,
        "last_error": task.last_error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "actual_duration": task.actual_duration,
        "workbook_count": workbook_count,
        "cancel_requested": cancel_requested,
        "cancel_requested_at": cancel_requested_at,
    }


class TaskQueryRepository:
    """Read-only task projections used by the HTTP delivery layer."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        *,
        statuses: list[TaskStatus] | None,
        task_types: list[TaskType] | None,
        country_code: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = []
        if statuses:
            filters.append(Task.status.in_(statuses))
        if task_types:
            filters.append(Task.task_type.in_(task_types))
        if country_code:
            filters.append(
                Task.country_id.in_(
                    select(Country.id).where(func.upper(Country.code) == country_code.strip().upper())
                )
            )
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

        count_query = select(func.count()).select_from(Task)
        if filters:
            count_query = count_query.where(*filters)
        total = int((await self.db.execute(count_query)).scalar_one() or 0)

        query = (
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
            query = query.where(*filters)
        rows = (await self.db.execute(query)).all()
        return [
            _task_projection(
                row.Task,
                country_code=row.country_code,
                country_name=row.country_name,
                workbook_count=int(row.workbook_count or 0),
            )
            for row in rows
        ], total

    async def detail(self, task_uuid: str) -> dict[str, Any] | None:
        row = (
            await self.db.execute(
                select(Task, Country.code.label("country_code"), Country.name_en.label("country_name"))
                .outerjoin(Country, Country.id == Task.country_id)
                .where(Task.task_uuid == task_uuid)
            )
        ).one_or_none()
        if row is None:
            return None
        workbooks = (
            await self.db.execute(
                select(TaskWorkbook)
                .where(TaskWorkbook.task_id == row.Task.id)
                .order_by(TaskWorkbook.created_at)
            )
        ).scalars().all()
        return {
            **_task_projection(
                row.Task,
                country_code=row.country_code,
                country_name=row.country_name,
                workbook_count=len(workbooks),
            ),
            "input_data": row.Task.input_data,
            "output_data": row.Task.output_data,
            "parent_task_id": row.Task.parent_task_id,
            "workbook_entries": [
                {
                    "id": entry.id,
                    "entry_uuid": entry.entry_uuid,
                    "entry_type": entry.entry_type,
                    "title": entry.title,
                    "content": entry.content,
                    "content_type": entry.content_type,
                    "prompt": entry.prompt,
                    "response": entry.response,
                    "model_used": entry.model_used,
                    "tokens_used": entry.tokens_used,
                    "cost": entry.cost,
                    "duration": entry.duration,
                    "success": entry.success,
                    "error_message": entry.error_message,
                    "metadata": entry.metadata_ or {},
                    "created_at": entry.created_at,
                }
                for entry in workbooks
            ],
        }

    async def worker_status(self, concurrency: int) -> dict[str, Any]:
        row = (
            await self.db.execute(
                select(
                    func.count().filter(Task.status == TaskStatus.QUEUED).label("queued_tasks"),
                    func.count().filter(Task.status == TaskStatus.RUNNING).label("running_tasks"),
                    func.count().filter(Task.status == TaskStatus.RETRYING).label("retrying_tasks"),
                    func.max(Task.created_at).label("latest_created_at"),
                    func.max(Task.started_at).label("latest_started_at"),
                    func.max(Task.completed_at).label("latest_completed_at"),
                )
            )
        ).one()
        services, _ = await runtime_registry.list_services()
        workers = [item for item in services if item.get("service") == "worker"]
        running = int(row.running_tasks or 0)
        retrying = int(row.retrying_tasks or 0)
        return {
            "worker_process_running": bool(workers),
            "worker_pid": workers[0].get("pid") if workers else None,
            "worker_concurrency": concurrency,
            "queued_tasks": int(row.queued_tasks or 0),
            "running_tasks": running,
            "retrying_tasks": retrying,
            "active_tasks": running + retrying,
            "latest_created_at": row.latest_created_at,
            "latest_started_at": row.latest_started_at,
            "latest_completed_at": row.latest_completed_at,
        }


class CountryQueryRepository:
    """Resolve stable country codes at the application boundary."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def id_for_code(self, country_code: str | None) -> int | None:
        if not country_code:
            return None
        value = (
            await self.db.execute(
                select(Country.id).where(
                    func.upper(Country.code) == country_code.strip().upper()
                )
            )
        ).scalar_one_or_none()
        return int(value) if value is not None else None


class ScheduleApplicationService:
    """Unify ingestion and release schedules behind stable string identifiers."""

    adapters = {
        "ingestion": automation_service,
        "release": data_release_service,
        "literature": literature_service,
    }

    @staticmethod
    def schedule_id(kind: str, job_id: str) -> str:
        return f"{kind}:{job_id}"

    @classmethod
    def parse_id(cls, schedule_id: str) -> tuple[str, str]:
        kind, separator, job_id = schedule_id.partition(":")
        if not separator or kind not in cls.adapters or not job_id:
            raise ValueError("Schedule id must use a supported '<kind>:<job-id>' identifier")
        return kind, job_id

    async def list(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in self.adapters:
            raise ValueError(f"Unsupported schedule kind: {kind}")
        kinds = (kind,) if kind else tuple(self.adapters)
        result: list[dict[str, Any]] = []
        for current_kind in kinds:
            snapshot = await self.adapters[current_kind].snapshot_async()
            for job in snapshot.get("jobs", []):
                result.append(
                    {
                        "id": self.schedule_id(current_kind, str(job["job_id"])),
                        "kind": current_kind,
                        "job_id": str(job["job_id"]),
                        "name": str(job.get("name") or job["job_id"]),
                        "enabled": bool(job.get("enabled")),
                        "country_code": job.get("country_code"),
                        "timezone": job.get("timezone") or snapshot.get("timezone"),
                        "interval_minutes": job.get("interval_minutes"),
                        "daily_time": job.get("daily_time"),
                        "next_run_at": job.get("next_run_at"),
                        "last_started_at": job.get("last_started_at"),
                        "last_finished_at": job.get("last_finished_at"),
                        "last_status": str(job.get("last_status") or "idle"),
                        "last_error": job.get("last_error"),
                        "last_task_uuid": job.get("last_task_uuid"),
                        "configuration": {
                            key: value
                            for key, value in job.items()
                            if key
                            not in {
                                "job_id",
                                "name",
                                "enabled",
                                "country_code",
                                "timezone",
                                "interval_minutes",
                                "daily_time",
                                "next_run_at",
                                "last_started_at",
                                "last_finished_at",
                                "last_status",
                                "last_error",
                                "last_task_uuid",
                            }
                        },
                    }
                )
        return sorted(result, key=lambda item: (item["kind"], item["name"], item["id"]))

    async def get(self, schedule_id: str) -> dict[str, Any] | None:
        kind, _ = self.parse_id(schedule_id)
        return next((item for item in await self.list(kind=kind) if item["id"] == schedule_id), None)

    async def trigger(self, schedule_id: str) -> dict[str, Any]:
        kind, job_id = self.parse_id(schedule_id)
        if kind == "ingestion":
            result = await automation_service.trigger_job(job_id, manual=True)
        elif kind == "release":
            result = await data_release_service.trigger_job(job_id, manual=True, trigger="manual")
        elif job_id == literature_service.GAP_DISCOVERY_JOB_ID:
            result = await literature_service.trigger_gap_discovery(manual=True)
        elif job_id == literature_service.ENRICHMENT_JOB_ID:
            result = await literature_service.trigger_enrichment(manual=True)
        else:
            result = await literature_service.trigger_job(job_id, manual=True)
        task_uuid = result.get("task_uuid")
        await control_plane_events.publish(
            "schedule.triggered",
            resource_type="schedule",
            resource_id=schedule_id,
            data={**result, "schedule_id": schedule_id, "kind": kind},
        )
        return {
            "task_uuid": task_uuid,
            "status": str(result.get("status") or "queued"),
            "resource_type": "task",
            "resource_id": task_uuid,
            "href": f"/operations/tasks?task={task_uuid}" if task_uuid else "/operations/tasks",
        }


class TaskOperationsService:
    """Task commands with an explicit transaction boundary."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        task_type: TaskType,
        task_name: str,
        country_code: str | None,
        priority: Any,
        description: str | None,
        input_data: dict[str, Any],
    ) -> Task | None:
        country_id = None
        if country_code:
            country_id = (
                await self.db.execute(
                    select(Country.id).where(
                        func.upper(Country.code) == country_code.strip().upper()
                    )
                )
            ).scalar_one_or_none()
            if country_id is None:
                return None
        return await task_manager.create_task(
            task_type=task_type,
            task_name=task_name,
            country_id=country_id,
            priority=priority,
            description=description,
            input_data={
                **input_data,
                **({"country_code": country_code.strip().upper()} if country_code else {}),
            },
        )

    async def events(self, task_uuid: str) -> list[dict[str, Any]] | None:
        task = (
            await self.db.execute(select(Task).where(Task.task_uuid == task_uuid))
        ).scalar_one_or_none()
        if task is None:
            return None
        entries = (
            await self.db.execute(
                select(TaskWorkbook)
                .where(TaskWorkbook.task_id == task.id)
                .order_by(TaskWorkbook.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": entry.entry_uuid,
                "type": entry.entry_type,
                "title": entry.title,
                "content": entry.content,
                "success": bool(entry.success),
                "error": entry.error_message,
                "occurred_at": entry.created_at,
                "metadata": entry.metadata_ or {},
            }
            for entry in entries
        ]

    async def retry(self, task_uuid: str) -> dict[str, Any] | None:
        task = (
            await self.db.execute(
                select(Task).where(Task.task_uuid == task_uuid).with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            return None
        if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise RuntimeError(f"Task status '{_value(task.status)}' cannot be retried")

        metadata = dict(task.metadata_ or {})
        for key in ("cancel_requested", "cancel_requested_at", "cancel_reason"):
            metadata.pop(key, None)
        metadata["retried_at"] = datetime.utcnow().isoformat() + "Z"
        task.metadata_ = metadata
        task.status = TaskStatus.QUEUED
        task.progress = 0
        task.completed_steps = 0
        task.started_at = None
        task.completed_at = None
        task.actual_duration = None
        task.last_error = None
        await self.db.commit()
        await self.db.refresh(task)
        await control_plane_events.publish_task_event(
            {
                "event": "task_status",
                "task_uuid": task.task_uuid,
                "status": "queued",
                "progress": 0,
                "retry": True,
            }
        )
        return {
            "task_uuid": task.task_uuid,
            "status": "queued",
            "resource_type": "task",
            "resource_id": task.task_uuid,
            "href": f"/operations/tasks?task={task.task_uuid}",
        }

    async def cancel(self, task_uuid: str) -> Task | None:
        task = (
            await self.db.execute(select(Task).where(Task.task_uuid == task_uuid))
        ).scalar_one_or_none()
        if task is None:
            return None
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise RuntimeError(
                f"Task status is '{_value(task.status)}'; this task can no longer be cancelled"
            )
        return await task_manager.request_task_cancel(task_uuid)


class SourceQueryRepository:
    """Stable country-code keyed ingestion source read model."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self) -> list[dict[str, Any]]:
        countries = (
            await self.db.execute(
                select(Country).where(Country.is_active.is_(True)).order_by(Country.code.asc())
            )
        ).scalars().all()
        schedule_rows = await schedule_application_service.list(kind="ingestion")
        schedules_by_country: dict[str, list[dict[str, Any]]] = {}
        for schedule in schedule_rows:
            code = str(schedule.get("country_code") or "").upper()
            schedules_by_country.setdefault(code, []).append(schedule)
        return [
            {
                "id": country.code,
                "country_code": country.code,
                "name": country.name_en or country.name or country.code,
                "enabled": bool(country.is_active),
                "schedule_count": len(schedules_by_country.get(country.code.upper(), [])),
                "enabled_schedule_count": sum(
                    1 for item in schedules_by_country.get(country.code.upper(), []) if item["enabled"]
                ),
                "last_task_uuid": next(
                    (
                        item["last_task_uuid"]
                        for item in schedules_by_country.get(country.code.upper(), [])
                        if item.get("last_task_uuid")
                    ),
                    None,
                ),
            }
            for country in countries
        ]

    async def runs(
        self,
        country_code: str,
        page_size: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int] | None:
        country = (
            await self.db.execute(
                select(Country).where(Country.code == country_code.upper())
            )
        ).scalar_one_or_none()
        if country is None:
            return None
        total = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(Task).where(
                        Task.country_id == country.id,
                        Task.task_type == TaskType.CRAWL_DATA,
                    )
                )
            ).scalar_one()
            or 0
        )
        tasks = (
            await self.db.execute(
                select(Task)
                .where(Task.country_id == country.id, Task.task_type == TaskType.CRAWL_DATA)
                .order_by(Task.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
        return [
            {
                "task_uuid": task.task_uuid,
                "status": _value(task.status),
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "last_error": task.last_error,
            }
            for task in tasks
        ], total


class SourceOperationsService:
    """Commands for country-code keyed ingestion sources."""

    _iceland_history_scopes = {"is_doh_history", "is_doh_legacy_icd"}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue_run(self, country_code: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        country = (
            await self.db.execute(
                select(Country).where(func.upper(Country.code) == country_code.strip().upper())
            )
        ).scalar_one_or_none()
        if country is None:
            return None

        normalized_code = country.code.upper()
        source = canonicalize_task_source(
            str(payload.get("source") or "all"),
            country_code=normalized_code,
        )
        fill_missing = bool(payload.get("fill_missing", False))
        start_year = payload.get("start_year")
        if normalized_code == "IS" and fill_missing:
            raise ValueError(
                "Iceland mixed-grain sources preserve missing periods as unknown; "
                "fill_missing is not supported"
            )
        if normalized_code == "IS" and source in self._iceland_history_scopes and start_year is not None:
            raise ValueError(
                "start_year applies only to Iceland current dashboards; reviewed history "
                "sources always process their complete workbook catalogue"
            )

        result = await crawl_task_service.enqueue_crawl_task(
            country_id=country.id,
            source=source,
            force=bool(payload.get("force", False)),
            process=bool(payload.get("process", True)),
            save_raw=bool(payload.get("save_raw", True)),
            fill_missing=fill_missing,
            include_current_month=payload.get("include_current_month"),
            revision_window_months=payload.get("revision_window_months"),
            priority=str(payload.get("priority") or "normal"),
            metadata={
                **({"start_year": start_year} if start_year is not None else {}),
                **({"source_file": payload["source_file"]} if payload.get("source_file") else {}),
                **({"source_dir": payload["source_dir"]} if payload.get("source_dir") else {}),
            }
            or None,
        )
        if not result.created:
            raise RuntimeError(
                f"A crawl task is already running for this country (task {result.task.task_uuid})"
            )
        task = result.task
        return {
            "task_uuid": task.task_uuid,
            "status": _value(task.status),
            "resource_type": "task",
            "resource_id": task.task_uuid,
            "href": f"/operations/tasks?task={task.task_uuid}",
        }


class ScheduleRunQueryRepository:
    """Read task history associated with a stable schedule id."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        schedule_id: str,
        page_size: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        kind, job_id = ScheduleApplicationService.parse_id(schedule_id)
        task_type = {
            "ingestion": TaskType.CRAWL_DATA,
            "release": TaskType.EXPORT_DATA,
            "literature": TaskType.SYNC_LITERATURE,
        }[kind]
        if kind == "literature" and job_id == literature_service.GAP_DISCOVERY_JOB_ID:
            task_type = TaskType.DISCOVER_LITERATURE_GAPS
        elif kind == "literature" and job_id == literature_service.ENRICHMENT_JOB_ID:
            task_type = TaskType.ENRICH_LITERATURE
        tasks = (
            await self.db.execute(
                select(Task)
                .where(Task.task_type == task_type)
                .order_by(Task.created_at.desc())
            )
        ).scalars().all()
        metadata_key = {
            "ingestion": "automation_job_id",
            "release": "release_job_id",
            "literature": "literature_job_id",
        }[kind]
        matching: list[dict[str, Any]] = []
        for task in tasks:
            source = {}
            if isinstance(task.input_data, dict):
                source.update(task.input_data)
            if isinstance(task.metadata_, dict):
                source.update(task.metadata_)
            if str(source.get(metadata_key) or "") != job_id:
                continue
            matching.append(
                {
                    "task_uuid": task.task_uuid,
                    "status": _value(task.status),
                    "created_at": task.created_at,
                    "started_at": task.started_at,
                    "completed_at": task.completed_at,
                    "last_error": task.last_error,
                }
            )
        total = len(matching)
        return matching[offset : offset + page_size], total


schedule_application_service = ScheduleApplicationService()

__all__ = [
    "CountryQueryRepository",
    "ScheduleApplicationService",
    "ScheduleRunQueryRepository",
    "SourceQueryRepository",
    "SourceOperationsService",
    "TaskOperationsService",
    "TaskQueryRepository",
    "schedule_application_service",
]
