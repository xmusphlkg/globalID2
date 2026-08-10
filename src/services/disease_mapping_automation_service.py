"""Resumable background automation for mapping suggestions and notifications."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from src.core import get_database, get_logger
from src.domain import SourceDiseaseCategory
from src.services.disease_mapping_ai_service import disease_mapping_ai_service
from src.services.disease_mapping_registry_service import disease_mapping_registry_service
from src.services.mapping_notification_service import mapping_notification_service

logger = get_logger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class DiseaseMappingAutomationService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_cycle: dict = {}

    @property
    def enabled(self) -> bool:
        return _env_bool("MAPPING_AUTOMATION_ENABLED", True)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
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
        poll_seconds = max(5, int(os.getenv("MAPPING_AUTOMATION_POLL_SECONDS", "15")))
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
        ai_limit = max(1, min(10, int(os.getenv("MAPPING_AI_BATCH_SIZE", "2"))))
        now = datetime.now(timezone.utc)
        async with get_database() as db:
            category_ids = list(
                (
                    await db.execute(
                        select(SourceDiseaseCategory.id)
                        .where(
                            SourceDiseaseCategory.ai_status.in_(["pending", "failed", "no_model"]),
                            SourceDiseaseCategory.ai_attempts < 5,
                            or_(
                                SourceDiseaseCategory.ai_next_attempt_at.is_(None),
                                SourceDiseaseCategory.ai_next_attempt_at <= now,
                            ),
                        )
                        .order_by(SourceDiseaseCategory.first_seen_at, SourceDiseaseCategory.id)
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
            result.get("status") in {"completed", "no_model", "not_required"}
            for result in results
        )
        ai_failed = sum(result.get("status") == "failed" for result in results)
        email = await mapping_notification_service.process_once(limit=50)
        return {
            "at": datetime.now(timezone.utc).isoformat(),
            "ai_claimed": len(category_ids),
            "ai_completed": ai_completed,
            "ai_failed": ai_failed,
            "ai_results": results,
            "email": email,
        }

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": bool(self._task and not self._task.done()),
            "last_cycle": self._last_cycle,
            "email_provider": os.getenv("MAPPING_EMAIL_PROVIDER", "smtp").strip().lower(),
        }


disease_mapping_automation_service = DiseaseMappingAutomationService()


__all__ = ["DiseaseMappingAutomationService", "disease_mapping_automation_service"]
