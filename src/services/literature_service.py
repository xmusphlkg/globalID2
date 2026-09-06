"""Scheduler adapter and task entry point for Research Radar synchronization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, union

from src.control_plane.schedule_state import schedule_state_repository
from src.ai.model_center import is_provider_authentication_error
from src.core import get_config, get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    LiteratureArticle,
    LiteratureSummary,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.literature import LiteraturePipeline
from src.literature.enrichment import LiteratureEnrichmentPipeline


logger = get_logger(__name__)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value).lower()


async def _count_catch_up_exception_backlog() -> int:
    """Count distinct articles with a current article or summary review exception.

    This deliberately avoids historical autopilot counters. An article in
    publication review with one or two review summaries is one editorial work
    item, not two or three. Explicit ``defer``/``archive`` decisions are
    inactive; missing or unknown decision metadata stays active fail-safe.
    """

    article_decision = LiteratureArticle.metadata_["autopilot"]["decision"].as_string()
    summary_decision = LiteratureSummary.generation_metadata["autopilot"]["decision"].as_string()
    article_reviews = select(LiteratureArticle.article_id.label("article_id")).where(
        LiteratureArticle.publication_status == "review",
        or_(
            article_decision.is_(None),
            article_decision.not_in(("defer", "archive")),
        ),
    )
    summary_reviews = select(LiteratureSummary.article_id.label("article_id")).where(
        LiteratureSummary.status == "review",
        or_(
            summary_decision.is_(None),
            summary_decision.not_in(("defer", "archive")),
        ),
    )
    distinct_review_articles = union(article_reviews, summary_reviews).subquery()
    async with get_database() as db:
        value = (
            await db.execute(
                select(func.count()).select_from(distinct_review_articles)
            )
        ).scalar_one()
    return int(value or 0)


def _at_or_before(value: datetime | None, upper_bound: datetime) -> bool:
    """Compare persisted timestamps defensively across SQLite/Postgres timezone shapes."""
    if value is None:
        return False
    candidate = value
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=upper_bound.tzinfo)
    return candidate <= upper_bound


@dataclass
class LiteratureScheduleState:
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str = "idle"
    last_error: str | None = None
    last_task_uuid: str | None = None


class LiteratureService:
    JOB_ID = "research-radar"
    ENRICHMENT_JOB_ID = "research-radar-enrichment"
    GAP_DISCOVERY_JOB_ID = "research-radar-gap-discovery"

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._state = LiteratureScheduleState()
        self._enrichment_state = LiteratureScheduleState()
        self._gap_state = LiteratureScheduleState()

    def _config(self):
        return get_config().literature

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.enabled or not (
            cfg.schedule_enabled
            or cfg.gap_discovery_schedule_enabled
            or ((cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled) and cfg.ai_enrichment_schedule_enabled)
        ):
            logger.info("Research Radar scheduler disabled by configuration")
            return
        schedule_states = await schedule_state_repository.load("literature")
        persisted = schedule_states.get(self.JOB_ID, {})
        for field, value in persisted.items():
            setattr(self._state, field, value)
        enrichment_persisted = schedule_states.get(self.ENRICHMENT_JOB_ID, {})
        for field, value in enrichment_persisted.items():
            setattr(self._enrichment_state, field, value)
        gap_persisted = schedule_states.get(self.GAP_DISCOVERY_JOB_ID, {})
        for field, value in gap_persisted.items():
            setattr(self._gap_state, field, value)
        if cfg.schedule_enabled and self._state.next_run_at is None:
            self._state.next_run_at = self._next_run()
        if (cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled) and cfg.ai_enrichment_schedule_enabled and self._enrichment_state.next_run_at is None:
            self._enrichment_state.next_run_at = self._next_enrichment_run()
        if cfg.gap_discovery_schedule_enabled and self._gap_state.next_run_at is None:
            self._gap_state.next_run_at = self._next_gap_run()
        await schedule_state_repository.save("literature", self.JOB_ID, self._state)
        await schedule_state_repository.save("literature", self.ENRICHMENT_JOB_ID, self._enrichment_state)
        await schedule_state_repository.save("literature", self.GAP_DISCOVERY_JOB_ID, self._gap_state)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="globalid-literature-scheduler")
        logger.info("Research Radar scheduler started")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        cfg = self._config()
        while not self._stop_event.is_set():
            # A worker can pull the next core run forward after committing a
            # truncated checkpoint. Refresh that one persisted timestamp so a
            # six-hour-old in-memory cadence cannot suppress catch-up work.
            if cfg.schedule_enabled and getattr(cfg, "catch_up_enabled", False):
                persisted = (await schedule_state_repository.load("literature")).get(
                    self.JOB_ID,
                    {},
                )
                if persisted.get("next_run_at") is not None:
                    self._state.next_run_at = persisted["next_run_at"]
            now = datetime.now(ZoneInfo(cfg.timezone))
            if self._state.next_run_at is None:
                self._state.next_run_at = self._next_run(now)
            if cfg.schedule_enabled and self._state.next_run_at and now >= self._state.next_run_at:
                try:
                    await self.trigger_job(self.JOB_ID, manual=False)
                except Exception:
                    logger.exception("Research Radar scheduled synchronization failed")
            if (cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled) and cfg.ai_enrichment_schedule_enabled:
                enrichment_persisted = (
                    await schedule_state_repository.load("literature")
                ).get(self.ENRICHMENT_JOB_ID, {})
                if enrichment_persisted.get("next_run_at") is not None:
                    self._enrichment_state.next_run_at = enrichment_persisted["next_run_at"]
                if self._enrichment_state.next_run_at is None:
                    self._enrichment_state.next_run_at = self._next_enrichment_run(now)
                if now >= self._enrichment_state.next_run_at:
                    try:
                        await self.trigger_enrichment(manual=False)
                    except Exception:
                        logger.exception("Research Radar scheduled enrichment failed")
            if cfg.gap_discovery_schedule_enabled and self._gap_state.next_run_at is None:
                self._gap_state.next_run_at = self._next_gap_run(now)
            if cfg.gap_discovery_schedule_enabled and self._gap_state.next_run_at and now >= self._gap_state.next_run_at:
                try:
                    await self.trigger_gap_discovery(manual=False)
                except Exception:
                    logger.exception("Research Radar scheduled evidence-gap discovery failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=cfg.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _next_run(self, now: datetime | None = None) -> datetime:
        cfg = self._config()
        current = now or datetime.now(ZoneInfo(cfg.timezone))
        return current + timedelta(minutes=cfg.interval_minutes)

    def _next_gap_run(self, now: datetime | None = None) -> datetime:
        cfg = self._config()
        current = now or datetime.now(ZoneInfo(cfg.timezone))
        return current + timedelta(minutes=cfg.gap_discovery_interval_minutes)

    def _next_enrichment_run(self, now: datetime | None = None) -> datetime:
        cfg = self._config()
        current = now or datetime.now(ZoneInfo(cfg.timezone))
        return current + timedelta(minutes=cfg.ai_enrichment_interval_minutes)

    def _next_enrichment_catch_up_run(self, now: datetime | None = None) -> datetime:
        cfg = self._config()
        current = now or datetime.now(ZoneInfo(cfg.timezone))
        return current + timedelta(
            minutes=getattr(cfg, "ai_enrichment_catch_up_interval_minutes", 1)
        )

    async def trigger_job(self, job_id: str, *, manual: bool, since: datetime | None = None) -> dict[str, Any]:
        if job_id != self.JOB_ID:
            raise ValueError(f"Literature job not found: {job_id}")
        if not self._config().enabled:
            raise ValueError("Research Radar is disabled")
        async with self._lock:
            async with get_database() as db:
                active = (
                    await db.execute(
                        select(Task)
                        .where(
                            Task.task_type == TaskType.SYNC_LITERATURE,
                            Task.status.in_((TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)),
                        )
                        .order_by(Task.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if active is not None:
                return {"job_id": job_id, "status": "skipped", "task_uuid": active.task_uuid, "reason": "A sync is already active"}

            now = datetime.now(ZoneInfo(self._config().timezone))
            self._state.last_started_at = now
            self._state.last_status = "running"
            self._state.last_error = None
            try:
                task = await task_manager.create_task(
                    task_type=TaskType.SYNC_LITERATURE,
                    task_name="Refresh GIDS Research Radar",
                    priority=TaskPriority.NORMAL,
                    status=TaskStatus.QUEUED,
                    description="Incremental Crossref and Europe PMC metadata synchronization",
                    input_data={
                        "literature_job_id": self.JOB_ID,
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                        **({"since": since.isoformat()} if since else {}),
                    },
                    tags=["research-radar", "literature"],
                )
                self._state.last_task_uuid = task.task_uuid
                self._state.last_status = "queued"
                self._state.last_finished_at = datetime.now(ZoneInfo(self._config().timezone))
                self._state.next_run_at = self._next_run(self._state.last_finished_at)
                return {"job_id": job_id, "status": "queued", "task_uuid": task.task_uuid}
            except Exception as exc:
                self._state.last_finished_at = datetime.now(ZoneInfo(self._config().timezone))
                self._state.last_status = "failed"
                self._state.last_error = str(exc)
                self._state.next_run_at = self._next_run(self._state.last_finished_at)
                raise
            finally:
                await schedule_state_repository.save("literature", self.JOB_ID, self._state)

    async def execute_task(self, task: Task) -> dict[str, Any]:
        cfg = self._config()
        result = await LiteraturePipeline(cfg).execute(task)
        catch_up_required = bool(result.get("source_truncated"))
        result["catch_up_required"] = int(catch_up_required)
        result["catch_up_scheduled"] = 0
        result["catch_up_paused_backpressure"] = 0
        result["catch_up_status"] = "not_required"
        result["catch_up_next_action_code"] = "none"
        result["catch_up_backlog_observed_count"] = None
        result["catch_up_backlog_limit"] = int(
            getattr(cfg, "catch_up_max_exception_backlog", 500)
        )
        max_records = int(getattr(cfg, "max_records_per_run", 300))
        # Catch-up is allowed only when observed + one worst-case batch is
        # strictly below the hard limit. This is the exact resume boundary,
        # surfaced so an operator does not have to reverse-engineer ``>=``.
        result["catch_up_resume_below_backlog"] = (
            result["catch_up_backlog_limit"] - max_records
        )
        scheduled_trigger = bool((task.input_data or {}).get("scheduled_trigger"))
        if not catch_up_required:
            return result
        if not scheduled_trigger:
            result["catch_up_status"] = "waiting_for_scheduled_trigger"
            result["catch_up_next_action_code"] = "await_next_scheduled_sync"
            return result
        if not cfg.schedule_enabled or not getattr(cfg, "catch_up_enabled", False):
            result["catch_up_status"] = "disabled"
            result["catch_up_next_action_code"] = "enable_accelerated_catch_up"
            return result

        backlog_limit = result["catch_up_backlog_limit"]
        try:
            backlog_count = await _count_catch_up_exception_backlog()
        except Exception as exc:
            result["catch_up_paused_backpressure"] = 1
            result["catch_up_backpressure_reason"] = "backlog_measurement_unavailable"
            result["catch_up_status"] = "paused_backlog_measurement"
            result["catch_up_next_action_code"] = "retry_backlog_measurement"
            logger.warning(
                "Research Radar catch-up paused because backlog measurement failed error_type={}",
                type(exc).__name__ or "Exception",
            )
            return result
        result["catch_up_backlog_observed_count"] = backlog_count
        projected_upper_bound = backlog_count + max_records
        result["catch_up_backlog_projected_upper_bound"] = projected_upper_bound
        if projected_upper_bound >= backlog_limit:
            result["catch_up_paused_backpressure"] = 1
            result["catch_up_backpressure_reason"] = "exception_backlog_headroom_exhausted"
            result["catch_up_status"] = "paused_backpressure"
            result["catch_up_next_action_code"] = (
                "reduce_exception_backlog_below_resume_threshold"
            )
            result["catch_up_required_backlog_reduction"] = max(
                0,
                backlog_count - result["catch_up_resume_below_backlog"] + 1,
            )
            logger.info(
                "Research Radar catch-up paused by backpressure backlog_count={} projected_upper_bound={} limit={} resume_below={}",
                backlog_count,
                projected_upper_bound,
                backlog_limit,
                result["catch_up_resume_below_backlog"],
            )
            return result
        now = datetime.now(ZoneInfo(cfg.timezone))
        next_run_at = now + timedelta(
            minutes=getattr(cfg, "catch_up_interval_minutes", 5)
        )
        advanced = await schedule_state_repository.schedule_earlier(
            "literature",
            self.JOB_ID,
            next_run_at,
        )
        if advanced:
            self._state.next_run_at = next_run_at
            result["catch_up_status"] = "scheduled"
            result["catch_up_next_action_code"] = "await_accelerated_catch_up"
            logger.info(
                "Research Radar catch-up scheduled next_run_at={} remaining_index_span_seconds={}",
                next_run_at.isoformat(),
                int(result.get("source_remaining_index_span_seconds") or 0),
            )
            result["catch_up_scheduled"] = 1
            result["catch_up_next_run_at"] = _iso(next_run_at)
            return result

        # ``schedule_earlier`` returns false both when an equal/earlier durable
        # schedule already exists and when its fail-closed DB update could not
        # be made. Confirm which state we are in rather than reporting a silent
        # no-op.
        persisted = (await schedule_state_repository.load("literature")).get(
            self.JOB_ID,
            {},
        )
        persisted_next_run = persisted.get("next_run_at")
        if _at_or_before(persisted_next_run, next_run_at):
            self._state.next_run_at = persisted_next_run
            result["catch_up_status"] = "already_scheduled"
            result["catch_up_next_action_code"] = "await_accelerated_catch_up"
            result["catch_up_next_run_at"] = _iso(persisted_next_run)
        else:
            result["catch_up_status"] = "schedule_persistence_unavailable"
            result["catch_up_next_action_code"] = "inspect_scheduler_persistence"
            result["catch_up_backpressure_reason"] = "schedule_persistence_unavailable"
            logger.warning(
                "Research Radar catch-up could not confirm a durable earlier schedule"
            )
        return result

    async def trigger_enrichment(
        self,
        *,
        article_ids: list[str] | None = None,
        languages: list[str] | None = None,
        limit: int | None = None,
        force: bool = False,
        manual: bool = True,
    ) -> dict[str, Any]:
        cfg = self._config()
        if not (cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled):
            raise ValueError("Literature AI enrichment and weekly AI review are disabled")
        async with self._lock:
            async with get_database() as db:
                active = (
                    await db.execute(
                        select(Task)
                        .where(
                            Task.task_type == TaskType.ENRICH_LITERATURE,
                            Task.status.in_((TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)),
                        )
                        .order_by(Task.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if active is not None:
                if not manual:
                    self._enrichment_state.next_run_at = self._next_enrichment_catch_up_run()
                    await schedule_state_repository.save(
                        "literature", self.ENRICHMENT_JOB_ID, self._enrichment_state
                    )
                return {
                    "job_id": self.ENRICHMENT_JOB_ID,
                    "status": "skipped",
                    "task_uuid": active.task_uuid,
                    "reason": "An enrichment task is already active",
                }
            now = datetime.now(ZoneInfo(cfg.timezone))
            self._enrichment_state.last_started_at = now
            self._enrichment_state.last_status = "running"
            self._enrichment_state.last_error = None
            try:
                mode = (
                    "summaries_and_weekly_ai_review"
                    if cfg.ai_enrichment_enabled and cfg.weekly_ai_review_enabled
                    else "weekly_ai_review"
                    if cfg.weekly_ai_review_enabled
                    else "summaries"
                )
                weekly_only = mode == "weekly_ai_review"
                task = await task_manager.create_task(
                    task_type=TaskType.ENRICH_LITERATURE,
                    task_name=(
                        "Review Research Radar weekly briefs with AI"
                        if weekly_only
                        else "Generate and review Research Radar evidence"
                        if mode == "summaries_and_weekly_ai_review"
                        else "Generate Research Radar evidence summaries"
                    ),
                    priority=TaskPriority.NORMAL,
                    status=TaskStatus.QUEUED,
                    description=(
                        "Content-bound public-evidence AI review; never an editorial signature"
                        if weekly_only
                        else "Model-center grounded summaries and content-bound weekly quality review"
                        if mode == "summaries_and_weekly_ai_review"
                        else "Model-center grounded summaries with automatic evidence-quality gates"
                    ),
                    input_data={
                        "mode": mode,
                        "article_ids": article_ids or [],
                        "languages": languages or cfg.ai_enrichment_languages,
                        "limit": limit or cfg.ai_enrichment_batch_size,
                        "force": force,
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                    },
                    tags=[
                        "research-radar", "literature",
                        "weekly-ai-review" if weekly_only else "ai-enrichment",
                        "autopilot",
                    ],
                )
                self._enrichment_state.last_task_uuid = task.task_uuid
                self._enrichment_state.last_status = "queued"
                self._enrichment_state.last_finished_at = datetime.now(ZoneInfo(cfg.timezone))
                self._enrichment_state.next_run_at = self._next_enrichment_run(
                    self._enrichment_state.last_finished_at
                )
                return {"job_id": self.ENRICHMENT_JOB_ID, "status": "queued", "task_uuid": task.task_uuid}
            except Exception as exc:
                self._enrichment_state.last_finished_at = datetime.now(ZoneInfo(cfg.timezone))
                self._enrichment_state.last_status = "failed"
                self._enrichment_state.last_error = str(exc)
                self._enrichment_state.next_run_at = self._next_enrichment_run(
                    self._enrichment_state.last_finished_at
                )
                raise
            finally:
                await schedule_state_repository.save(
                    "literature", self.ENRICHMENT_JOB_ID, self._enrichment_state
                )

    async def execute_enrichment_task(self, task: Task) -> dict[str, Any]:
        cfg = self._config()
        mode = str((task.input_data or {}).get("mode") or "summaries")
        if mode not in {"summaries", "weekly_ai_review", "summaries_and_weekly_ai_review"}:
            raise ValueError("unsupported_literature_enrichment_mode")
        output: dict[str, Any] = {"mode": mode}
        if mode in {"summaries", "summaries_and_weekly_ai_review"}:
            if cfg.ai_enrichment_enabled:
                output["summaries"] = await LiteratureEnrichmentPipeline(cfg).execute(task)
            else:
                output["summaries"] = {"status": "disabled"}
        if mode in {"weekly_ai_review", "summaries_and_weekly_ai_review"}:
            if not cfg.weekly_ai_review_enabled:
                output["weekly_ai_review"] = {"status": "disabled"}
            else:
                from src.literature.weekly_ai_review import review_weekly_brief_files

                output["weekly_ai_review"] = await review_weekly_brief_files(
                    limit=cfg.weekly_ai_review_batch_size,
                    preferred_models=list((task.input_data or {}).get("preferred_models") or []),
                    timeout_seconds=cfg.weekly_ai_review_timeout_seconds,
                    max_attempts=cfg.weekly_ai_review_max_attempts,
                    apply=True,
                )
                if output["weekly_ai_review"]["counts"]["failed"]:
                    output["weekly_ai_review"]["status"] = "failed_closed"
                    output["weekly_ai_review"]["error"] = "weekly_brief_ai_review_failed_closed"
                    if mode == "weekly_ai_review":
                        raise RuntimeError("weekly_brief_ai_review_failed_closed")
                    logger.warning(
                        "Research Radar weekly AI review failed during combined enrichment; summary catch-up will continue"
                    )
        await self._schedule_enrichment_catch_up_if_needed(task, output)
        return output

    async def _schedule_enrichment_catch_up_if_needed(
        self,
        task: Task,
        output: dict[str, Any],
    ) -> None:
        cfg = self._config()
        output["ai_enrichment_catch_up_required"] = 0
        output["ai_enrichment_catch_up_scheduled"] = 0
        output["ai_enrichment_catch_up_blocked_provider_auth"] = 0
        output["ai_enrichment_catch_up_status"] = "not_required"
        output["ai_enrichment_catch_up_next_action_code"] = "none"
        if not (cfg.ai_enrichment_enabled and cfg.ai_enrichment_schedule_enabled):
            output["ai_enrichment_catch_up_status"] = "disabled"
            return
        input_data = task.input_data or {}
        if input_data.get("article_ids") or bool(input_data.get("force", False)):
            output["ai_enrichment_catch_up_status"] = "targeted_run"
            return
        summaries = output.get("summaries")
        if not isinstance(summaries, dict):
            return
        selected = int(summaries.get("articles") or 0)
        requested_limit = min(
            max(1, int(input_data.get("limit") or cfg.ai_enrichment_batch_size)),
            cfg.ai_enrichment_batch_size,
        )
        output["ai_enrichment_catch_up_selected_articles"] = selected
        output["ai_enrichment_catch_up_batch_limit"] = requested_limit
        if selected < requested_limit:
            return

        output["ai_enrichment_catch_up_required"] = 1
        provider_auth_failure = bool(summaries.get("provider_auth_failure"))
        if not provider_auth_failure:
            provider_auth_failure = any(
                is_provider_authentication_error(item.get("error"))
                for item in summaries.get("errors", [])
                if isinstance(item, dict)
            )
        if provider_auth_failure:
            output["ai_enrichment_catch_up_blocked_provider_auth"] = 1
            output["ai_enrichment_catch_up_status"] = "blocked_provider_auth"
            output["ai_enrichment_catch_up_next_action_code"] = "refresh_credentials_and_test"
            logger.warning(
                "Research Radar enrichment catch-up blocked because provider credentials were rejected"
            )
            return

        now = datetime.now(ZoneInfo(cfg.timezone))
        next_run_at = self._next_enrichment_catch_up_run(now)
        advanced = await schedule_state_repository.schedule_earlier(
            "literature",
            self.ENRICHMENT_JOB_ID,
            next_run_at,
        )
        if advanced:
            self._enrichment_state.next_run_at = next_run_at
            output["ai_enrichment_catch_up_scheduled"] = 1
            output["ai_enrichment_catch_up_status"] = "scheduled"
            output["ai_enrichment_catch_up_next_action_code"] = "await_accelerated_enrichment"
            output["ai_enrichment_catch_up_next_run_at"] = _iso(next_run_at)
            logger.info(
                "Research Radar enrichment catch-up scheduled next_run_at={} selected_articles={} batch_limit={}",
                next_run_at.isoformat(),
                selected,
                requested_limit,
            )
            return

        persisted = (await schedule_state_repository.load("literature")).get(
            self.ENRICHMENT_JOB_ID,
            {},
        )
        persisted_next_run = persisted.get("next_run_at")
        if _at_or_before(persisted_next_run, next_run_at):
            self._enrichment_state.next_run_at = persisted_next_run
            output["ai_enrichment_catch_up_status"] = "already_scheduled"
            output["ai_enrichment_catch_up_next_action_code"] = "await_accelerated_enrichment"
            output["ai_enrichment_catch_up_next_run_at"] = _iso(persisted_next_run)
        else:
            output["ai_enrichment_catch_up_status"] = "schedule_persistence_unavailable"
            output["ai_enrichment_catch_up_next_action_code"] = "inspect_scheduler_persistence"
            logger.warning(
                "Research Radar enrichment catch-up could not confirm a durable earlier schedule"
            )

    async def trigger_gap_discovery(
        self,
        *,
        manual: bool,
        gap_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        cfg = self._config()
        if not cfg.gap_discovery_enabled:
            raise ValueError("Literature evidence-gap discovery is disabled")
        async with self._lock:
            async with get_database() as db:
                active = (
                    await db.execute(
                        select(Task)
                        .where(
                            Task.task_type == TaskType.DISCOVER_LITERATURE_GAPS,
                            Task.status.in_((TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)),
                        )
                        .order_by(Task.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if active is not None:
                return {
                    "job_id": self.GAP_DISCOVERY_JOB_ID,
                    "status": "skipped",
                    "task_uuid": active.task_uuid,
                    "reason": "An evidence-gap discovery task is already active",
                }
            now = datetime.now(ZoneInfo(cfg.timezone))
            self._gap_state.last_started_at = now
            self._gap_state.last_status = "running"
            self._gap_state.last_error = None
            try:
                task = await task_manager.create_task(
                    task_type=TaskType.DISCOVER_LITERATURE_GAPS,
                    task_name="Discover evidence for Research Radar gaps",
                    priority=TaskPriority.HIGH,
                    status=TaskStatus.QUEUED,
                    description="Targeted Crossref and Europe PMC discovery with automatic evidence gates",
                    input_data={
                        "literature_job_id": self.GAP_DISCOVERY_JOB_ID,
                        "gap_ids": gap_ids or [],
                        "limit": limit or cfg.gap_discovery_max_gaps_per_run,
                        "scheduled_trigger": not manual,
                        "manual_trigger": manual,
                    },
                    tags=["research-radar", "literature", "evidence-gap", "autopilot"],
                )
                self._gap_state.last_task_uuid = task.task_uuid
                self._gap_state.last_status = "queued"
                self._gap_state.last_finished_at = datetime.now(ZoneInfo(cfg.timezone))
                self._gap_state.next_run_at = self._next_gap_run(self._gap_state.last_finished_at)
                return {"job_id": self.GAP_DISCOVERY_JOB_ID, "status": "queued", "task_uuid": task.task_uuid}
            except Exception as exc:
                self._gap_state.last_finished_at = datetime.now(ZoneInfo(cfg.timezone))
                self._gap_state.last_status = "failed"
                self._gap_state.last_error = str(exc)
                self._gap_state.next_run_at = self._next_gap_run(self._gap_state.last_finished_at)
                raise
            finally:
                await schedule_state_repository.save(
                    "literature", self.GAP_DISCOVERY_JOB_ID, self._gap_state
                )

    async def execute_gap_discovery_task(self, task: Task) -> dict[str, Any]:
        from src.services.literature_gap_service import literature_gap_service

        return await literature_gap_service.execute(task)

    async def snapshot_async(self) -> dict[str, Any]:
        cfg = self._config()
        now = datetime.now(ZoneInfo(cfg.timezone))
        state_changed: list[tuple[str, LiteratureScheduleState]] = []
        persisted = (await schedule_state_repository.load("literature")).get(self.JOB_ID, {})
        for field, value in persisted.items():
            setattr(self._state, field, value)
        async with get_database() as db:
            latest = (
                await db.execute(
                    select(Task)
                    .where(Task.task_type == TaskType.SYNC_LITERATURE)
                    .order_by(Task.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest is not None:
            self._state.last_task_uuid = latest.task_uuid
            self._state.last_status = _value(latest.status)
            self._state.last_started_at = latest.started_at or self._state.last_started_at
            self._state.last_finished_at = latest.completed_at or self._state.last_finished_at
            self._state.last_error = latest.last_error
        if cfg.schedule_enabled and self._state.next_run_at is None:
            self._state.next_run_at = self._next_run(now)
            state_changed.append((self.JOB_ID, self._state))
        enrichment_persisted = (await schedule_state_repository.load("literature")).get(self.ENRICHMENT_JOB_ID, {})
        for field, value in enrichment_persisted.items():
            setattr(self._enrichment_state, field, value)
        async with get_database() as db:
            latest_enrichment = (
                await db.execute(
                    select(Task)
                    .where(Task.task_type == TaskType.ENRICH_LITERATURE)
                    .order_by(Task.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest_enrichment is not None:
            self._enrichment_state.last_task_uuid = latest_enrichment.task_uuid
            self._enrichment_state.last_status = _value(latest_enrichment.status)
            self._enrichment_state.last_started_at = latest_enrichment.started_at or self._enrichment_state.last_started_at
            self._enrichment_state.last_finished_at = latest_enrichment.completed_at or self._enrichment_state.last_finished_at
            self._enrichment_state.last_error = latest_enrichment.last_error
        if (cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled) and cfg.ai_enrichment_schedule_enabled and self._enrichment_state.next_run_at is None:
            self._enrichment_state.next_run_at = self._next_enrichment_run(now)
            state_changed.append((self.ENRICHMENT_JOB_ID, self._enrichment_state))
        gap_persisted = (await schedule_state_repository.load("literature")).get(self.GAP_DISCOVERY_JOB_ID, {})
        for field, value in gap_persisted.items():
            setattr(self._gap_state, field, value)
        async with get_database() as db:
            latest_gap = (
                await db.execute(
                    select(Task)
                    .where(Task.task_type == TaskType.DISCOVER_LITERATURE_GAPS)
                    .order_by(Task.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest_gap is not None:
            self._gap_state.last_task_uuid = latest_gap.task_uuid
            self._gap_state.last_status = _value(latest_gap.status)
            self._gap_state.last_started_at = latest_gap.started_at or self._gap_state.last_started_at
            self._gap_state.last_finished_at = latest_gap.completed_at or self._gap_state.last_finished_at
            self._gap_state.last_error = latest_gap.last_error
        if cfg.gap_discovery_schedule_enabled and self._gap_state.next_run_at is None:
            self._gap_state.next_run_at = self._next_gap_run(now)
            state_changed.append((self.GAP_DISCOVERY_JOB_ID, self._gap_state))
        for job_id, state in state_changed:
            await schedule_state_repository.save("literature", job_id, state)
        return {
            "enabled": cfg.enabled,
            "timezone": cfg.timezone,
            "jobs": [{
                "job_id": self.JOB_ID,
                "name": "Research Radar literature sync",
                "enabled": cfg.enabled,
                "country_code": None,
                "timezone": cfg.timezone,
                "interval_minutes": cfg.interval_minutes if cfg.schedule_enabled else None,
                "daily_time": None,
                "next_run_at": _iso(self._state.next_run_at) if cfg.schedule_enabled else None,
                "last_started_at": _iso(self._state.last_started_at),
                "last_finished_at": _iso(self._state.last_finished_at),
                "last_status": self._state.last_status,
                "last_error": self._state.last_error,
                "last_task_uuid": self._state.last_task_uuid,
                "schedule_enabled": cfg.schedule_enabled,
                "source": "Crossref + Europe PMC" if cfg.europe_pmc_enabled else "Crossref",
                "max_records_per_run": cfg.max_records_per_run,
                "catch_up_enabled": getattr(cfg, "catch_up_enabled", False),
                "catch_up_interval_minutes": getattr(cfg, "catch_up_interval_minutes", None),
                "catch_up_max_exception_backlog": getattr(
                    cfg,
                    "catch_up_max_exception_backlog",
                    None,
                ),
                "catch_up_resume_below_backlog": (
                    getattr(cfg, "catch_up_max_exception_backlog", 500)
                    - cfg.max_records_per_run
                ),
                "catch_up_required": bool(
                    (latest.output_data or {}).get("catch_up_required")
                    if latest is not None and isinstance(latest.output_data, dict)
                    else False
                ),
                "catch_up_status": (
                    (latest.output_data or {}).get("catch_up_status")
                    if latest is not None and isinstance(latest.output_data, dict)
                    else "unknown"
                ),
                "catch_up_next_action_code": (
                    (latest.output_data or {}).get("catch_up_next_action_code")
                    if latest is not None and isinstance(latest.output_data, dict)
                    else "inspect_latest_sync_result"
                ),
                "catch_up_next_run_at": (
                    (latest.output_data or {}).get("catch_up_next_run_at")
                    if latest is not None and isinstance(latest.output_data, dict)
                    else None
                ),
                "ai_enrichment_enabled": cfg.ai_enrichment_enabled,
                "ai_enrichment_languages": cfg.ai_enrichment_languages,
                "ai_enrichment_batch_size": cfg.ai_enrichment_batch_size,
            }, {
                "job_id": self.GAP_DISCOVERY_JOB_ID,
                "name": "Research Radar evidence-gap discovery",
                "enabled": cfg.gap_discovery_enabled,
                "country_code": None,
                "timezone": cfg.timezone,
                "interval_minutes": cfg.gap_discovery_interval_minutes if cfg.gap_discovery_schedule_enabled else None,
                "daily_time": None,
                "next_run_at": _iso(self._gap_state.next_run_at) if cfg.gap_discovery_schedule_enabled else None,
                "last_started_at": _iso(self._gap_state.last_started_at),
                "last_finished_at": _iso(self._gap_state.last_finished_at),
                "last_status": self._gap_state.last_status,
                "last_error": self._gap_state.last_error,
                "last_task_uuid": self._gap_state.last_task_uuid,
                "schedule_enabled": cfg.gap_discovery_schedule_enabled,
                "source": "Crossref + Europe PMC targeted queries",
                "max_gaps_per_run": cfg.gap_discovery_max_gaps_per_run,
                "records_per_gap": cfg.gap_discovery_records_per_gap,
                "candidate_limit": cfg.gap_discovery_candidate_limit,
                "review_required": not cfg.autopilot_enabled,
                "autopilot_enabled": cfg.autopilot_enabled,
            }, {
                "job_id": self.ENRICHMENT_JOB_ID,
                "name": "Research Radar evidence enrichment",
                "enabled": cfg.ai_enrichment_enabled or cfg.weekly_ai_review_enabled,
                "country_code": None,
                "timezone": cfg.timezone,
                "interval_minutes": cfg.ai_enrichment_interval_minutes if cfg.ai_enrichment_schedule_enabled else None,
                "daily_time": None,
                "next_run_at": _iso(self._enrichment_state.next_run_at) if cfg.ai_enrichment_schedule_enabled else None,
                "last_started_at": _iso(self._enrichment_state.last_started_at),
                "last_finished_at": _iso(self._enrichment_state.last_finished_at),
                "last_status": self._enrichment_state.last_status,
                "last_error": self._enrichment_state.last_error,
                "last_task_uuid": self._enrichment_state.last_task_uuid,
                "schedule_enabled": cfg.ai_enrichment_schedule_enabled,
                "source": "Model Center evidence agent",
                "batch_size": cfg.ai_enrichment_batch_size,
                "languages": cfg.ai_enrichment_languages,
                "catch_up_interval_minutes": getattr(
                    cfg,
                    "ai_enrichment_catch_up_interval_minutes",
                    None,
                ),
                "catch_up_required": bool(
                    (latest_enrichment.output_data or {}).get("ai_enrichment_catch_up_required")
                    if latest_enrichment is not None and isinstance(latest_enrichment.output_data, dict)
                    else False
                ),
                "catch_up_status": (
                    (latest_enrichment.output_data or {}).get("ai_enrichment_catch_up_status")
                    if latest_enrichment is not None and isinstance(latest_enrichment.output_data, dict)
                    else "unknown"
                ),
                "catch_up_next_action_code": (
                    (latest_enrichment.output_data or {}).get("ai_enrichment_catch_up_next_action_code")
                    if latest_enrichment is not None and isinstance(latest_enrichment.output_data, dict)
                    else "inspect_latest_enrichment_result"
                ),
                "catch_up_next_run_at": (
                    (latest_enrichment.output_data or {}).get("ai_enrichment_catch_up_next_run_at")
                    if latest_enrichment is not None and isinstance(latest_enrichment.output_data, dict)
                    else None
                ),
                "weekly_ai_review_enabled": cfg.weekly_ai_review_enabled,
                "weekly_ai_review_batch_size": cfg.weekly_ai_review_batch_size,
                "review_required": not cfg.autopilot_enabled,
                "autopilot_enabled": cfg.autopilot_enabled,
            }],
        }


literature_service = LiteratureService()

__all__ = ["LiteratureService", "literature_service"]
