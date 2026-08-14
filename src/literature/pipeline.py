"""End-to-end incremental Research Radar synchronization pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid
from typing import Any

from sqlalchemy import select

from src.core.database import get_db
from src.core.logging import get_logger
from src.core.task_manager import task_manager
from src.domain import Country, LiteratureIngestRun, StandardDisease, Task

from .classification import classify_candidate
from .clients import CrossrefClient, EuropePmcClient
from .normalization import apply_europe_pmc, normalize_crossref
from .repository import LiteratureRepository


logger = get_logger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _parse_checkpoint_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class LiteraturePipeline:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def execute(self, task: Task | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        since = await self._resolve_since(now, task)
        run_uuid = str(uuid.uuid4())
        await self._create_run(run_uuid, since, now)
        try:
            journals_payload = _load_json(ROOT / self.config.journals_path)
            journals = [item for item in journals_payload.get("journals") or [] if item.get("issn")]
            crossref = CrossrefClient(
                mailto=self.config.contact_email,
                timeout_seconds=self.config.request_timeout_seconds,
                retries=self.config.max_retries,
            )
            source_result = await crossref.fetch_incremental(
                journals=journals,
                since=since,
                until=now,
                max_records=self.config.max_records_per_run,
                concurrency=self.config.source_concurrency,
            )
            raw_records = source_result.records
            candidates = [candidate for item in raw_records if (candidate := normalize_crossref(item))]
            if task:
                await task_manager.update_task_progress(task.task_uuid, 35)
                if await task_manager.is_cancel_requested(task.task_uuid):
                    raise RuntimeError("Literature synchronization cancelled")

            if self.config.europe_pmc_enabled:
                dois = [candidate.doi for candidate in candidates if candidate.doi]
                enrichment = await EuropePmcClient(
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                ).enrich_by_dois(dois[: self.config.max_europe_pmc_records])
                for candidate in candidates:
                    if candidate.doi and candidate.doi in enrichment:
                        apply_europe_pmc(candidate, enrichment[candidate.doi])
            if task:
                await task_manager.update_task_progress(task.task_uuid, 55)

            taxonomy = _load_json(ROOT / self.config.taxonomy_path)
            diseases, countries = await self._classification_catalogues()
            inserted = updated = excluded = published = 0
            async with get_db() as db:
                repository = LiteratureRepository(db)
                for index, candidate in enumerate(candidates):
                    classification = classify_candidate(
                        candidate,
                        diseases=diseases,
                        countries=countries,
                        taxonomy=taxonomy,
                        now=now,
                        auto_publish_min_score=self.config.auto_publish_min_score,
                    )
                    # Autopilot owns publication so that every automatic decision
                    # passes the complete metadata/integrity gate and is audited.
                    if self.config.autopilot_enabled and classification.publication_status == "published":
                        classification.publication_status = "review"
                    was_inserted = await repository.upsert(candidate, classification)
                    inserted += int(was_inserted)
                    updated += int(not was_inserted)
                    excluded += int(classification.publication_status == "excluded")
                    published += int(classification.publication_status == "published")
                    if task and index % 20 == 0:
                        await task_manager.update_task_progress(
                            task.task_uuid,
                            min(95, 55 + int(40 * (index + 1) / max(1, len(candidates)))),
                        )
                await db.commit()

            automation = None
            if self.config.autopilot_enabled:
                from src.services.literature_automation_service import literature_automation_service

                automation = await literature_automation_service.reconcile()

            counts = {
                "fetched": len(raw_records),
                "normalized": len(candidates),
                "inserted": inserted,
                "updated": updated,
                "published": published,
                "requires_review": len(candidates) - excluded - published,
                "excluded": excluded,
                "autopilot_changed": int((automation or {}).get("changed") or 0),
                "source_records_seen": int(source_result.checkpoint.get("records_seen") or len(raw_records)),
                "source_records_returned": int(source_result.checkpoint.get("records_returned") or len(raw_records)),
                "source_truncated": int(bool(source_result.checkpoint.get("truncated"))),
            }
            through_indexed_at = _parse_checkpoint_datetime(source_result.checkpoint.get("through_indexed_at")) or now
            await self._finish_run(
                run_uuid,
                "completed",
                counts=counts,
                checkpoint=source_result.checkpoint,
                through_indexed_at=through_indexed_at,
            )
            if task:
                await task_manager.update_task_progress(task.task_uuid, 100)
            return {
                "run_uuid": run_uuid,
                "from_indexed_at": since.isoformat(),
                "through_indexed_at": through_indexed_at.isoformat(),
                **counts,
                "automation": automation,
            }
        except Exception as exc:
            await self._finish_run(run_uuid, "failed", error=str(exc))
            raise

    async def _resolve_since(self, now: datetime, task: Task | None) -> datetime:
        requested = (task.input_data or {}).get("since") if task else None
        if requested:
            parsed = datetime.fromisoformat(str(requested).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        async with get_db() as db:
            latest = (
                await db.execute(
                    select(LiteratureIngestRun)
                    .where(LiteratureIngestRun.status == "completed")
                    .order_by(LiteratureIngestRun.through_indexed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest and latest.through_indexed_at:
            checkpoint = latest.checkpoint or {}
            if checkpoint.get("truncated"):
                next_from = _parse_checkpoint_datetime(checkpoint.get("next_from_indexed_at"))
                if next_from is not None:
                    return next_from
            value = latest.through_indexed_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value - timedelta(days=self.config.index_overlap_days)
        return now - timedelta(days=self.config.initial_lookback_days)

    async def _classification_catalogues(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        alias_payload = _load_json(ROOT / self.config.disease_aliases_path)
        aliases_by_id = alias_payload.get("aliases") or {}
        async with get_db() as db:
            disease_rows = (
                await db.execute(select(StandardDisease).where(StandardDisease.is_active.is_(True)))
            ).scalars().all()
            country_rows = (
                await db.execute(select(Country).where(Country.is_active.is_(True)))
            ).scalars().all()
        diseases = [
            {
                "disease_id": row.disease_id,
                "name_en": row.standard_name_en,
                "name_zh": row.standard_name_zh,
                "aliases": [
                    *[str(value) for value in aliases_by_id.get(row.disease_id, [])],
                    *[str(value) for value in (row.metadata_ or {}).get("aliases", [])],
                ],
            }
            for row in disease_rows
        ]
        countries = [
            {
                "code": row.code,
                "name": row.name,
                "name_en": row.name_en or row.name,
                "name_zh": str((row.metadata_ or {}).get("name_zh") or ""),
            }
            for row in country_rows
        ]
        return diseases, countries

    async def _create_run(self, run_uuid: str, since: datetime, through: datetime) -> None:
        async with get_db() as db:
            db.add(LiteratureIngestRun(
                run_uuid=run_uuid,
                source="crossref+europe-pmc" if self.config.europe_pmc_enabled else "crossref",
                status="running",
                started_at=datetime.now(timezone.utc),
                from_indexed_at=since,
                through_indexed_at=through,
                checkpoint={"strategy": "index-date", "overlap_days": self.config.index_overlap_days},
                counts={},
            ))
            await db.commit()

    async def _finish_run(
        self,
        run_uuid: str,
        status: str,
        *,
        counts: dict[str, int] | None = None,
        checkpoint: dict[str, Any] | None = None,
        through_indexed_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        async with get_db() as db:
            run = (
                await db.execute(select(LiteratureIngestRun).where(LiteratureIngestRun.run_uuid == run_uuid))
            ).scalar_one()
            run.status = status
            run.completed_at = datetime.now(timezone.utc)
            if through_indexed_at is not None:
                run.through_indexed_at = through_indexed_at
            if checkpoint is not None:
                run.checkpoint = checkpoint
            run.counts = counts or {}
            run.error = error
            await db.commit()


__all__ = ["LiteraturePipeline"]
