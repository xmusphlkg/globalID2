"""Resumable background automation for mapping suggestions and notifications."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.model_center import get_active_model_routes
from src.core import get_database, get_logger
from src.control_plane.schedule_state import schedule_state_repository
from src.domain import ScheduledJobState, SourceDiseaseCategory, StandardDisease
from src.services.disease_mapping_ai_service import disease_mapping_ai_service
from src.services.disease_mapping_registry_service import disease_mapping_registry_service
from src.services.mapping_notification_service import mapping_notification_service

logger = get_logger(__name__)

_STATE_KIND = "mapping_automation"
_STATE_JOB_ID = "ai-suggestions"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _is_provider_circuit_error(error: object) -> bool:
    normalized = str(error or "").casefold()
    return any(
        marker in normalized
        for marker in (
            "insufficient_user_quota",
            "insufficient quota",
            "permissiondeniederror",
            "authenticationerror",
            "error code: 401",
            "error code: 403",
        )
    )


def _route_signature(routes: list[dict]) -> str:
    """Identify exactly the model routes used by mapping suggestions."""

    return "|".join(
        str(route.get("model_key") or route.get("model_name") or "unknown")
        for route in routes[:2]
    ) or "no-active-route"


def _all_results_are_provider_circuit_failures(results: list[dict]) -> bool:
    if not results:
        return False
    errors = [
        result.get("error") or result.get("provider_error")
        for result in results
        if result.get("status") in {"failed", "no_model"}
    ]
    return len(errors) == len(results) and all(
        error and _is_provider_circuit_error(error) for error in errors
    )


class DiseaseMappingAutomationService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_cycle: dict = {}
        self._ai_circuit_until: datetime | None = None
        self._ai_route_signature: str | None = None

    def _sync_route_signature(self, route_signature: str) -> None:
        if (
            self._ai_circuit_until is not None
            and self._ai_route_signature != route_signature
        ):
            logger.info(
                "Disease mapping AI routes changed ({} -> {}); clearing the "
                "provider circuit",
                self._ai_route_signature or "unknown",
                route_signature,
            )
            self._ai_circuit_until = None
        self._ai_route_signature = route_signature

    @property
    def enabled(self) -> bool:
        return _env_bool("MAPPING_AUTOMATION_ENABLED", True)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        persisted = (await schedule_state_repository.load(_STATE_KIND)).get(
            _STATE_JOB_ID
        )
        persisted_until = persisted.get("next_run_at") if persisted else None
        try:
            persisted_cycle = (
                json.loads(persisted.get("last_error") or "{}")
                if persisted
                else {}
            )
        except (TypeError, json.JSONDecodeError):
            persisted_cycle = {}
        self._ai_route_signature = str(
            persisted_cycle.get("ai_route_signature") or ""
        ) or None
        if (
            persisted
            and persisted.get("last_status") == "circuit_open"
            and persisted_until is not None
            and persisted_until > datetime.now(timezone.utc)
        ):
            self._ai_circuit_until = persisted_until
        async with get_database() as db:
            await disease_mapping_registry_service.ensure_schema(db)
            stale_before = datetime.now(timezone.utc) - timedelta(minutes=30)
            rows = (
                await db.execute(
                    select(SourceDiseaseCategory).where(
                        SourceDiseaseCategory.ai_status == "processing",
                        SourceDiseaseCategory.updated_at < stale_before.replace(tzinfo=None),
                    )
                )
            ).scalars().all()
            for row in rows:
                row.ai_status = "pending"
                row.ai_last_error = "Recovered stale mapping suggestion claim after restart"
                row.ai_next_attempt_at = None
            await db.commit()
        if not self.enabled:
            logger.info("Disease mapping automation is disabled")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="disease-mapping-automation")
        logger.info("Disease mapping automation started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("Disease mapping automation did not stop within 10 seconds")
        self._task = None

    async def _run(self) -> None:
        poll_seconds = _env_int(
            "MAPPING_AUTOMATION_POLL_SECONDS", 5, minimum=5, maximum=3600
        )
        while not self._stop.is_set():
            try:
                self._last_cycle = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Disease mapping automation cycle failed: {}", exc)
                self._last_cycle = {"error": str(exc), "at": datetime.now(timezone.utc).isoformat()}
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def process_once(self) -> dict:
        ai_limit = _env_int("MAPPING_AI_BATCH_SIZE", 6, minimum=1, maximum=10)
        now = datetime.now(timezone.utc)
        route_signature = _route_signature(await get_active_model_routes())
        self._sync_route_signature(route_signature)
        circuit_open = bool(
            self._ai_circuit_until and now < self._ai_circuit_until
        )
        category_ids: list[int] = []
        if not circuit_open:
            async with get_database() as db:
                exact_standard_name = exists(
                    select(StandardDisease.id).where(
                        StandardDisease.is_active.is_(True),
                        func.lower(StandardDisease.standard_name_en)
                        == func.lower(SourceDiseaseCategory.canonical_source_label),
                    )
                )
                category_ids = list(
                    (
                        await db.execute(
                            select(SourceDiseaseCategory.id)
                            .where(
                                SourceDiseaseCategory.ai_status.in_(["pending", "failed", "no_model"]),
                                or_(
                                    SourceDiseaseCategory.ai_next_attempt_at.is_(None),
                                    SourceDiseaseCategory.ai_next_attempt_at <= now,
                                ),
                            )
                            .order_by(
                                case((exact_standard_name, 0), else_=1),
                                SourceDiseaseCategory.first_seen_at,
                                SourceDiseaseCategory.id,
                            )
                            .limit(ai_limit)
                        )
                    ).scalars().all()
                )

        async def process_category(category_id: int) -> dict:
            try:
                async with get_database() as db:
                    return await disease_mapping_ai_service.suggest_for_category(db, category_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return {"category_id": category_id, "status": "failed", "error": str(exc)}

        results = await asyncio.gather(
            *(process_category(category_id) for category_id in category_ids)
        )
        ai_completed = sum(
            result.get("status") in {"completed", "not_required"}
            for result in results
        )
        ai_deferred = sum(result.get("status") == "no_model" for result in results)
        ai_failed = sum(result.get("status") == "failed" for result in results)
        if _all_results_are_provider_circuit_failures(results):
            cooldown_minutes = _env_int(
                "MAPPING_AI_PROVIDER_COOLDOWN_MINUTES",
                360,
                minimum=5,
                maximum=10080,
            )
            self._ai_circuit_until = datetime.now(timezone.utc) + timedelta(
                minutes=cooldown_minutes
            )
            circuit_open = True
            logger.error(
                "Disease mapping AI provider circuit opened until {} after "
                "authentication/quota failures",
                self._ai_circuit_until.isoformat(),
            )
        elif results and (ai_failed + ai_deferred) < len(results):
            self._ai_circuit_until = None
            circuit_open = False
        email = await mapping_notification_service.process_once()
        cycle = {
            "at": datetime.now(timezone.utc).isoformat(),
            "ai_claimed": len(category_ids),
            "ai_completed": ai_completed,
            "ai_deferred": ai_deferred,
            "ai_failed": ai_failed,
            "ai_results": results,
            "ai_route_signature": route_signature,
            "ai_circuit_open": circuit_open,
            "ai_circuit_until": (
                self._ai_circuit_until.isoformat()
                if self._ai_circuit_until
                else None
            ),
            "email": email,
        }
        await schedule_state_repository.save(
            _STATE_KIND,
            _STATE_JOB_ID,
            SimpleNamespace(
                next_run_at=self._ai_circuit_until,
                last_started_at=now,
                last_finished_at=datetime.now(timezone.utc),
                last_status=(
                    "circuit_open"
                    if circuit_open
                    else ("failed" if ai_failed else "completed")
                ),
                last_error=json.dumps(cycle, ensure_ascii=False, default=str),
                last_task_uuid=None,
            ),
        )
        return cycle

    async def snapshot_for_db(self, db: AsyncSession) -> dict:
        """Return scheduler-owned state to API processes through PostgreSQL."""

        row = (
            await db.execute(
                select(ScheduledJobState).where(
                    ScheduledJobState.job_kind == _STATE_KIND,
                    ScheduledJobState.job_id == _STATE_JOB_ID,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return self.snapshot()
        try:
            last_cycle = json.loads(row.last_error or "{}")
        except (TypeError, json.JSONDecodeError):
            last_cycle = {}
        now = datetime.now(timezone.utc)
        finished = row.last_finished_at
        if finished is not None and finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        poll_seconds = _env_int(
            "MAPPING_AUTOMATION_POLL_SECONDS", 5, minimum=5, maximum=3600
        )
        circuit_until = row.next_run_at
        if circuit_until is not None and circuit_until.tzinfo is None:
            circuit_until = circuit_until.replace(tzinfo=timezone.utc)
        return {
            "enabled": self.enabled,
            "running": bool(
                finished
                and (now - finished).total_seconds() <= max(90, poll_seconds * 3)
            ),
            "last_cycle": last_cycle,
            "email_provider": os.getenv("MAPPING_EMAIL_PROVIDER", "smtp")
            .strip()
            .lower(),
            "ai_circuit_open": bool(circuit_until and now < circuit_until),
            "ai_circuit_until": (
                circuit_until.isoformat() if circuit_until else None
            ),
        }

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": bool(self._task and not self._task.done()),
            "last_cycle": self._last_cycle,
            "email_provider": os.getenv("MAPPING_EMAIL_PROVIDER", "smtp").strip().lower(),
            "ai_circuit_open": bool(
                self._ai_circuit_until
                and datetime.now(timezone.utc) < self._ai_circuit_until
            ),
            "ai_circuit_until": (
                self._ai_circuit_until.isoformat()
                if self._ai_circuit_until
                else None
            ),
            "ai_route_signature": self._ai_route_signature,
        }


disease_mapping_automation_service = DiseaseMappingAutomationService()


__all__ = ["DiseaseMappingAutomationService", "disease_mapping_automation_service"]
