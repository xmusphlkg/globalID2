"""Helpers for creating and queueing crawl tasks from APIs and automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from src.core import get_database
from src.core.country_library import get_country_bootstrap_config
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
        include_current_month: Optional[bool] = None,
        revision_window_months: Optional[int] = None,
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
                Task.status.in_(
                    [TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.RETRYING]
                ),
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
        country_config = get_country_bootstrap_config(country.code)
        crawler_config = (
            country_config.get("crawler_config", {})
            if isinstance(country_config, dict)
            else {}
        )
        source_policies = crawler_config.get("source_policies", {})
        source_policy = (
            source_policies.get(normalized_source, {})
            if isinstance(source_policies, dict)
            else {}
        )
        if not isinstance(source_policy, dict):
            source_policy = {}
        effective_config = {**crawler_config, **source_policy}
        effective_include_current_month = (
            self._as_bool(
                effective_config.get("default_include_current_month"), False
            )
            if include_current_month is None
            else bool(include_current_month)
        )
        supports_current_month = self._as_bool(
            effective_config.get("supports_current_month"), False
        )
        if effective_include_current_month and not supports_current_month:
            raise ValueError(
                f"Current-month ingestion is not supported for {country.code.upper()}"
            )
        revision_unit = str(
            effective_config.get("revision_window_unit")
            or (
                "weeks"
                if str(effective_config.get("temporal_granularity") or effective_config.get("cadence") or "").lower() == "weekly"
                else "months"
            )
        ).lower()
        configured_revision_window = effective_config.get("default_revision_window")
        if configured_revision_window is None:
            configured_revision_window = (
                effective_config.get("refresh_recent_weeks", 12)
                if revision_unit == "weeks"
                else effective_config.get("refresh_recent_months", 3)
            )
        try:
            effective_revision_window = int(
                revision_window_months
                if revision_window_months is not None
                else configured_revision_window
            )
        except (TypeError, ValueError):
            effective_revision_window = 12 if revision_unit == "weeks" else 3
        effective_revision_window = max(
            1,
            min(52 if revision_unit == "weeks" else 24, effective_revision_window),
        )
        task = await task_manager.create_task(
            task_type=TaskType.CRAWL_DATA,
            task_name=f"Crawl {country.code.upper()} Data ({normalized_source})",
            country_id=country.id,
            priority=normalized_priority,
            description=description
            or (
                f"Source: {normalized_source}, Force: {'Yes' if force else 'No'}, "
                f"Process: {'Yes' if process else 'No'}, "
                f"Current Month: {'Yes' if effective_include_current_month else 'No'}, "
                f"Revision Window: {effective_revision_window} {revision_unit}"
            ),
            input_data={
                "country": country.code.upper(),
                "country_code": country.code.upper(),
                "source": normalized_source,
                "force": force,
                "process": process,
                "save_raw": save_raw,
                "fill_missing": fill_missing,
                "include_current_month": effective_include_current_month,
                "revision_window_months": effective_revision_window,
                "revision_window_unit": revision_unit,
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

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)


crawl_task_service = CrawlTaskService()
