"""Helpers for creating and queueing crawl tasks from APIs and automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from src.core import get_database
from src.core.source_scopes import canonicalize_task_source
from src.core.task_manager import task_manager
from src.domain import Country, Task, TaskPriority, TaskStatus, TaskType


@dataclass
class EnqueueCrawlTaskResult:
    task: Task
    created: bool
    skipped_reason: Optional[str] = None


class CrawlTaskService:
    """Create or queue crawl tasks with the same rules used by the dashboard."""

    async def enqueue_crawl_task(
        self,
        *,
        country_id: Optional[int] = None,
        country_code: Optional[str] = None,
        source: str = "all",
        force: bool = False,
        process: bool = True,
        save_raw: bool = True,
        fill_missing: bool = False,
        priority: str = "normal",
        description: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> EnqueueCrawlTaskResult:
        async with get_database() as db:
            country = await self._resolve_country(
                country_id=country_id,
                country_code=country_code,
                db=db,
            )
            if country is None:
                raise ValueError(f"Country not found: id={country_id!r} code={country_code!r}")

            running_q = select(Task).where(
                Task.task_type == TaskType.CRAWL_DATA,
                Task.country_id == country.id,
                Task.status.in_([TaskStatus.RUNNING, TaskStatus.QUEUED]),
            )
            existing = (await db.execute(running_q)).scalar_one_or_none()
            if existing is not None:
                return EnqueueCrawlTaskResult(
                    task=existing,
                    created=False,
                    skipped_reason="already_running",
                )

        normalized_source = canonicalize_task_source(source, country_code=country.code)
        normalized_priority = self._normalize_priority(priority)
        task = await task_manager.create_task(
            task_type=TaskType.CRAWL_DATA,
            task_name=f"Crawl {country.code.upper()} Data ({normalized_source})",
            country_id=country.id,
            priority=normalized_priority,
            description=description
            or (
                f"Source: {normalized_source}, Force: {'Yes' if force else 'No'}, "
                f"Process: {'Yes' if process else 'No'}"
            ),
            input_data={
                "country": country.code.upper(),
                "country_code": country.code.upper(),
                "source": normalized_source,
                "force": force,
                "process": process,
                "save_raw": save_raw,
                "fill_missing": fill_missing,
                **(metadata or {}),
            },
        )
        task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
        return EnqueueCrawlTaskResult(task=task, created=True)

    async def _resolve_country(
        self,
        *,
        country_id: Optional[int],
        country_code: Optional[str],
        db,
    ) -> Optional[Country]:
        if country_id is not None:
            return (await db.execute(select(Country).where(Country.id == country_id))).scalar_one_or_none()
        if country_code:
            return (
                await db.execute(
                    select(Country).where(Country.code == country_code.strip().upper())
                )
            ).scalar_one_or_none()
        return None

    @staticmethod
    def _normalize_priority(priority: str) -> TaskPriority:
        normalized = (priority or "normal").strip().lower()
        try:
            return TaskPriority(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported priority: {priority}") from exc


crawl_task_service = CrawlTaskService()
