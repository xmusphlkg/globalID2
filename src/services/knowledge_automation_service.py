"""Bounded, source-first autopilot for the disease knowledge backlog."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from src.control_plane.schedule_state import schedule_state_repository
from src.core import get_config, get_database, get_logger
from src.domain import Task, TaskStatus, TaskType
from src.knowledge.quality import EVIDENCE_POLICY_VERSION
from src.knowledge.profile_schema import knowledge_profile_schema_signature
from src.knowledge.sources import KNOWLEDGE_SOURCE_STRATEGY_VERSION
from src.services.disease_knowledge_service import (
    ACTIVE_KNOWLEDGE_TASK_STATUSES,
    DiseaseKnowledgeUpdateService,
)


logger = get_logger(__name__)

KNOWLEDGE_TASK_TYPES = (
    TaskType.UPDATE_DISEASE_KNOWLEDGE,
    TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
)


@dataclass
class KnowledgeAutomationState:
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str = "idle"
    last_error: str | None = None
    last_task_uuid: str | None = None


def knowledge_backlog_slots(
    active_count: int,
    *,
    target: int,
    batch_size: int,
) -> int:
    """Keep a bounded amount of work ready without flooding a personal plan."""

    return max(0, min(max(0, target - active_count), max(1, batch_size)))


def _task_disease_ids(task: Task) -> set[str]:
    payload = task.input_data if isinstance(task.input_data, dict) else {}
    values = payload.get("disease_ids")
    if not isinstance(values, list):
        values = [payload.get("disease_id") or payload.get("disease")]
    return {
        str(value).strip().upper()
        for value in values
        if str(value or "").strip()
    }


def _task_repair_priority(
    task: Task,
    *,
    canonical_priorities: dict[str, str],
) -> str:
    """Resolve live repair priority instead of trusting stale task payloads."""
    ranks = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "none": 4}
    priorities = [
        str(canonical_priorities.get(disease_id) or "").strip().lower()
        for disease_id in _task_disease_ids(task)
        if str(canonical_priorities.get(disease_id) or "").strip().lower() in ranks
    ]
    if priorities:
        return min(priorities, key=lambda value: ranks[value])
    payload = task.input_data if isinstance(task.input_data, dict) else {}
    return str(payload.get("repair_priority") or "").strip().lower()


def _source_discovery_exhausted(
    task: Task,
    *,
    evidence_policy_version: int = EVIDENCE_POLICY_VERSION,
    source_strategy_version: int = KNOWLEDGE_SOURCE_STRATEGY_VERSION,
    profile_schema_signature: str | None = None,
) -> bool:
    output = task.output_data if isinstance(task.output_data, dict) else {}
    metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
    task_policy_version = output.get("evidence_policy_version")
    if task_policy_version is None:
        task_policy_version = metadata.get("evidence_policy_version")
    try:
        policy_matches = int(task_policy_version) == evidence_policy_version
    except (TypeError, ValueError):
        policy_matches = False
    task_strategy_version = output.get("source_strategy_version")
    if task_strategy_version is None:
        task_strategy_version = metadata.get("source_strategy_version")
    try:
        strategy_matches = int(task_strategy_version) == source_strategy_version
    except (TypeError, ValueError):
        strategy_matches = False
    task_profile_signature = str(
        output.get("profile_schema_signature")
        or metadata.get("profile_schema_signature")
        or ""
    ).strip()
    profile_matches = (
        not profile_schema_signature
        or task_profile_signature == profile_schema_signature
    )
    return policy_matches and strategy_matches and profile_matches and bool(
        output.get("source_discovery_exhausted")
    )


def _source_transport_retry_context(
    task: Task,
    *,
    now: datetime,
    evidence_policy_version: int = EVIDENCE_POLICY_VERSION,
    source_strategy_version: int = KNOWLEDGE_SOURCE_STRATEGY_VERSION,
    profile_schema_signature: str | None = None,
) -> tuple[bool, dict[str, int] | None]:
    """Return whether a completed source task is delayed, plus retry context.

    Only an explicit transport state may delay a new task.  In particular, an
    old generic ``awaiting_evidence`` marker cannot block repairs after the
    source strategy changes or after a later successful source refresh.
    """

    output = task.output_data if isinstance(task.output_data, dict) else {}
    metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
    task_policy_version = output.get("evidence_policy_version", metadata.get("evidence_policy_version"))
    task_strategy_version = output.get("source_strategy_version", metadata.get("source_strategy_version"))
    try:
        versions_match = (
            int(task_policy_version) == evidence_policy_version
            and int(task_strategy_version) == source_strategy_version
        )
    except (TypeError, ValueError):
        versions_match = False
    task_profile_signature = str(
        output.get("profile_schema_signature")
        or metadata.get("profile_schema_signature")
        or ""
    ).strip()
    profile_matches = (
        not profile_schema_signature
        or task_profile_signature == profile_schema_signature
    )
    state = str(
        output.get("source_discovery_state")
        or metadata.get("source_discovery_state")
        or ""
    ).strip().lower()
    if not versions_match or not profile_matches or state != "awaiting_source_transport":
        return False, None

    raw_retry_after = output.get("source_retry_after") or metadata.get("source_retry_after")
    if not isinstance(raw_retry_after, str):
        return False, None
    try:
        retry_after = datetime.fromisoformat(raw_retry_after.replace("Z", "+00:00"))
    except ValueError:
        return False, None
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    try:
        attempt = max(1, int(output.get("source_transport_attempt") or metadata.get("source_transport_attempt") or 1))
    except (TypeError, ValueError):
        attempt = 1
    return retry_after > now, {"source_transport_attempt": attempt}


class KnowledgeAutomationService:
    """Maintain an idempotent source-first queue from the canonical catalogue."""

    JOB_KIND = "knowledge"
    JOB_ID = "disease-knowledge-autopilot"

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._state = KnowledgeAutomationState()

    def _config(self):
        return get_config().ai

    async def start(self) -> None:
        cfg = self._config()
        if not cfg.knowledge_automation_enabled:
            logger.info("Disease knowledge autopilot disabled by configuration")
            return
        persisted = (await schedule_state_repository.load(self.JOB_KIND)).get(
            self.JOB_ID,
            {},
        )
        for field, value in persisted.items():
            setattr(self._state, field, value)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="globalid-knowledge-autopilot",
        )
        logger.info(
            "Disease knowledge autopilot started (target_backlog={}, batch_size={})",
            cfg.knowledge_automation_backlog_target,
            cfg.knowledge_automation_batch_size,
        )

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

    async def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.JOB_ID,
            "enabled": bool(self._config().knowledge_automation_enabled),
            "next_run_at": self._state.next_run_at.isoformat()
            if self._state.next_run_at
            else None,
            "last_started_at": self._state.last_started_at.isoformat()
            if self._state.last_started_at
            else None,
            "last_finished_at": self._state.last_finished_at.isoformat()
            if self._state.last_finished_at
            else None,
            "last_status": self._state.last_status,
            "last_error": self._state.last_error,
            "last_task_uuid": self._state.last_task_uuid,
        }

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("Disease knowledge autopilot reconciliation failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config().knowledge_automation_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def reconcile_once(self) -> dict[str, Any]:
        """Top up true content gaps first, then older evidence-policy profiles."""

        cfg = self._config()
        async with self._lock:
            now = datetime.now(timezone.utc)
            self._state.last_started_at = now
            self._state.last_status = "running"
            self._state.last_error = None
            await schedule_state_repository.save(self.JOB_KIND, self.JOB_ID, self._state)
            try:
                async with get_database() as db:
                    service = DiseaseKnowledgeUpdateService()
                    priority_rebalance = await service.rebalance_active_repair_task_priorities(db)
                    active_tasks = list(
                        (
                            await db.execute(
                                select(Task).where(
                                    Task.task_type.in_(KNOWLEDGE_TASK_TYPES),
                                    Task.status.in_(ACTIVE_KNOWLEDGE_TASK_STATUSES),
                                )
                            )
                        ).scalars().all()
                    )
                    active_by_type = {
                        task_type.value: sum(
                            1 for task in active_tasks if task.task_type == task_type
                        )
                        for task_type in KNOWLEDGE_TASK_TYPES
                    }
                    catalogue = await service.list_catalogue(db)
                    canonical_priorities = {
                        str(item.get("disease_id") or "").strip().upper(): str(
                            item.get("repair_priority") or ""
                        ).strip().lower()
                        for item in catalogue
                        if str(item.get("disease_id") or "").strip()
                    }
                    canonical_profile_signatures = {
                        str(item.get("disease_id") or "").strip().upper(): knowledge_profile_schema_signature(item)
                        for item in catalogue
                        if str(item.get("disease_id") or "").strip()
                    }
                    active_source_count = active_by_type.get(
                        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES.value,
                        0,
                    )
                    active_model_count = active_by_type.get(
                        TaskType.UPDATE_DISEASE_KNOWLEDGE.value,
                        0,
                    )
                    active_content_gap_source_count = sum(
                        1
                        for task in active_tasks
                        if task.task_type == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES
                        and _task_repair_priority(
                            task,
                            canonical_priorities=canonical_priorities,
                        )
                        in {"urgent", "high"}
                    )
                    active_revalidation_model_count = sum(
                        1
                        for task in active_tasks
                        if task.task_type == TaskType.UPDATE_DISEASE_KNOWLEDGE
                        and _task_repair_priority(
                            task,
                            canonical_priorities=canonical_priorities,
                        )
                        in {"normal", "low"}
                    )
                    source_slots = knowledge_backlog_slots(
                        active_content_gap_source_count,
                        target=cfg.knowledge_automation_backlog_target,
                        batch_size=cfg.knowledge_automation_batch_size,
                    )
                    normal_revalidation_slots = knowledge_backlog_slots(
                        active_revalidation_model_count,
                        target=cfg.knowledge_automation_model_backlog_target,
                        batch_size=cfg.knowledge_automation_batch_size,
                    )
                    source_history_after = now - timedelta(
                        seconds=max(
                            cfg.knowledge_automation_evidence_retry_seconds,
                            cfg.knowledge_automation_source_retry_seconds,
                        )
                    )
                    completed_source_tasks = list(
                        (
                            await db.execute(
                                select(Task).where(
                                    Task.task_type == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                                    Task.status == TaskStatus.COMPLETED,
                                    Task.completed_at >= source_history_after,
                                )
                                .order_by(Task.completed_at.desc())
                            )
                        ).scalars().all()
                    )
                    latest_source_task_by_disease: dict[str, Task] = {}
                    for task in completed_source_tasks:
                        for disease_id in _task_disease_ids(task):
                            latest_source_task_by_disease.setdefault(disease_id, task)

                    retained_profile_recovery = await service.reconcile_retained_profiles(
                        db,
                    )
                    transport_profile_recovery = await service.restore_profiles_after_transient_source_transport(
                        db,
                        latest_source_task_by_disease,
                    )
                    source_gap_invalidation = await service.invalidate_profiles_with_unresolved_source_gaps(
                        db,
                        latest_source_task_by_disease,
                    )
                    if (
                        source_gap_invalidation["updated_brief_count"]
                        or retained_profile_recovery["restored_profile_count"]
                        or transport_profile_recovery["restored_brief_count"]
                    ):
                        priority_rebalance = await service.rebalance_active_repair_task_priorities(db)
                        catalogue = await service.list_catalogue(db)
                        canonical_priorities = {
                            str(item.get("disease_id") or "").strip().upper(): str(
                                item.get("repair_priority") or ""
                            ).strip().lower()
                            for item in catalogue
                            if str(item.get("disease_id") or "").strip()
                        }
                        canonical_profile_signatures = {
                            str(item.get("disease_id") or "").strip().upper(): knowledge_profile_schema_signature(item)
                            for item in catalogue
                            if str(item.get("disease_id") or "").strip()
                        }

                    evidence_deferred_ids: set[str] = set()
                    transport_deferred_ids: set[str] = set()
                    source_retry_context_by_disease: dict[str, dict[str, int]] = {}
                    for disease_id, task in latest_source_task_by_disease.items():
                        if _source_discovery_exhausted(
                            task,
                            evidence_policy_version=EVIDENCE_POLICY_VERSION,
                            source_strategy_version=KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                            profile_schema_signature=canonical_profile_signatures.get(
                                disease_id
                            ),
                        ):
                            evidence_deferred_ids.add(disease_id)
                            continue
                        transport_deferred, retry_context = _source_transport_retry_context(
                            task,
                            now=now,
                            evidence_policy_version=EVIDENCE_POLICY_VERSION,
                            source_strategy_version=KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                            profile_schema_signature=canonical_profile_signatures.get(
                                disease_id
                            ),
                        )
                        if retry_context is None:
                            continue
                        if transport_deferred:
                            transport_deferred_ids.add(disease_id)
                        else:
                            source_retry_context_by_disease[disease_id] = retry_context
                    deferred_ids = evidence_deferred_ids | transport_deferred_ids
                    primary = {
                        "created_count": 0,
                        "created_tasks": [],
                        "candidate_count": 0,
                        "skipped_count": 0,
                    }
                    secondary = None
                    if source_slots:
                        primary = await service.enqueue_repair_tasks(
                            db,
                            force=True,
                            limit=source_slots,
                            priorities={"urgent", "high"},
                            excluded_disease_ids=deferred_ids,
                            source_retry_context_by_disease=source_retry_context_by_disease,
                            source_first=True,
                            requested_by=self.JOB_ID,
                            initiated_via=self.JOB_ID,
                        )
                        normal_source_slots = knowledge_backlog_slots(
                            active_source_count + int(primary["created_count"]),
                            target=cfg.knowledge_automation_backlog_target,
                            batch_size=cfg.knowledge_automation_batch_size,
                        )
                        revalidation_limit = min(
                            normal_source_slots,
                            normal_revalidation_slots,
                        )
                        if (
                            revalidation_limit
                            and cfg.knowledge_automation_revalidate_stale
                        ):
                            secondary = await service.enqueue_repair_tasks(
                                db,
                                force=True,
                                limit=revalidation_limit,
                                priorities={"normal"},
                                excluded_disease_ids=deferred_ids,
                                source_retry_context_by_disease=source_retry_context_by_disease,
                                source_first=True,
                                requested_by=self.JOB_ID,
                                initiated_via=self.JOB_ID,
                            )

                created = [*(primary.get("created_tasks") or [])]
                if secondary:
                    created.extend(secondary.get("created_tasks") or [])
                self._state.last_task_uuid = created[-1].task_uuid if created else None
                self._state.last_finished_at = datetime.now(timezone.utc)
                self._state.next_run_at = self._state.last_finished_at + timedelta(
                    seconds=cfg.knowledge_automation_interval_seconds
                )
                self._state.last_status = "healthy"
                await schedule_state_repository.save(self.JOB_KIND, self.JOB_ID, self._state)
                result = {
                    "active_source_count": active_source_count,
                    "active_content_gap_source_count": active_content_gap_source_count,
                    "active_model_count": active_model_count,
                    "active_revalidation_model_count": active_revalidation_model_count,
                    "source_slots": source_slots,
                    "normal_revalidation_slots": normal_revalidation_slots,
                    "priority_rebalanced": int(priority_rebalance["changed"]),
                    "deferred_evidence_diseases": len(evidence_deferred_ids),
                    "deferred_source_transport_diseases": len(transport_deferred_ids),
                    "due_source_transport_retries": len(source_retry_context_by_disease),
                    "source_gap_profiles_demoted": int(
                        source_gap_invalidation["updated_brief_count"]
                    ),
                    "source_gap_profiles_retained": int(
                        source_gap_invalidation["retained_brief_count"]
                    ),
                    "source_gap_profiles_archived": int(
                        source_gap_invalidation["archived_profile_count"]
                    ),
                    "legacy_refresh_sources_restored": int(
                        retained_profile_recovery["restored_source_count"]
                    ),
                    "retained_evidence_profiles_restored": int(
                        retained_profile_recovery["restored_profile_count"]
                    ),
                    "orphaned_profiles_archived": int(
                        retained_profile_recovery["archived_profile_count"]
                    ),
                    "source_transport_profiles_restored": int(
                        transport_profile_recovery["restored_brief_count"]
                    ),
                    "content_gap_created": int(primary.get("created_count") or 0),
                    "revalidation_created": int(
                        (secondary or {}).get("created_count") or 0
                    ),
                }
                logger.info("Disease knowledge autopilot reconciliation: {}", result)
                return result
            except Exception as exc:
                self._state.last_finished_at = datetime.now(timezone.utc)
                self._state.next_run_at = self._state.last_finished_at + timedelta(
                    seconds=cfg.knowledge_automation_interval_seconds
                )
                self._state.last_status = "failed"
                self._state.last_error = str(exc)[:1000]
                await schedule_state_repository.save(self.JOB_KIND, self.JOB_ID, self._state)
                raise


knowledge_automation_service = KnowledgeAutomationService()


__all__ = [
    "KnowledgeAutomationService",
    "knowledge_automation_service",
    "knowledge_backlog_slots",
]
