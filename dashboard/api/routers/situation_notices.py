"""Control-plane endpoints for the notice-based Situation module.

Read views over the notice database (notices, AI assessments, events, weekly
digests, source runs) plus two operational actions: queue a pipeline run and
re-queue one notice for AI. Publication itself has no human gate; the peer
review recorded on each assessment is the decision.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.task_manager import task_manager
from src.domain import (
    SituationAIAssessment,
    SituationDigest,
    SituationEvent,
    SituationNotice,
    SituationNoticeEntity,
    SituationSourceRun,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.services.global_sources.catalogue import SOURCE_REGISTRY
from src.services.situation_notices.ai import DIGEST_PROMPT_VERSION, NOTICE_PROMPT_VERSION, review_mode

router = APIRouter(prefix="/situation")


class RefreshRequest(BaseModel):
    mode: Literal["full", "no_fetch", "metadata_only"] = "full"
    ai_limit: int = 100
    digest_weeks: int = 2


def _published(row: SituationNotice) -> SituationAIAssessment | None:
    return next((a for a in row.assessments if a.status == "published"), None)


def _assessment(row: SituationAIAssessment) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "prompt_version": row.prompt_version,
        "content_hash": row.content_hash,
        "title_zh": row.title_zh,
        "title_fr": row.title_fr,
        "summary_en": row.summary_en,
        "summary_zh": row.summary_zh,
        "summary_fr": row.summary_fr,
        "event_type": row.event_type,
        "risk_level": row.risk_level,
        "risk_rationale": row.risk_rationale,
        "disease_ids": row.disease_ids or [],
        "country_codes": row.country_codes or [],
        "author_model": row.author_model,
        "reviewer_model": row.reviewer_model,
        "review_mode": review_mode(row.author_model, row.reviewer_model),
        "review_verdict": row.review_verdict,
        "review_rounds": row.review_rounds,
        "review_notes": row.review_notes or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _notice(row: SituationNotice, *, detail: bool = False) -> dict:
    published = _published(row)
    payload = {
        "notice_id": row.notice_id,
        "source_id": row.source_id,
        "source_label": (SOURCE_REGISTRY.get(row.source_id, {}).get("label") or {}).get("en"),
        "record_type": row.record_type,
        "title_en": row.title_en,
        "title_zh": published.title_zh if published else None,
        "url": row.url,
        "published_date": row.published_date.isoformat(),
        "iso_week_key": row.iso_week_key,
        "redistribution": row.redistribution,
        "ai_status": row.ai_status,
        "ai_attempts": row.ai_attempts,
        "ai_last_error": row.ai_last_error,
        "ai_next_attempt_at": row.ai_next_attempt_at.isoformat() if row.ai_next_attempt_at else None,
        "risk_level": published.risk_level if published else None,
        "event_type": published.event_type if published else None,
        "review_mode": review_mode(published.author_model, published.reviewer_model) if published else None,
        "entities": [
            {
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "name_en": e.name_en,
                "name_zh": e.name_zh,
                "method": e.method,
                "active": e.active,
            }
            for e in row.entities
        ],
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }
    if detail:
        payload["summary_en"] = row.summary_en
        payload["content_hash"] = row.content_hash
        payload["assessments"] = [
            _assessment(a) for a in sorted(row.assessments, key=lambda a: a.id or 0, reverse=True)
        ]
    return payload


@router.get("/overview")
async def situation_overview() -> dict:
    async with get_db() as db:
        ai_counts = dict(
            (await db.execute(select(SituationNotice.ai_status, func.count()).group_by(SituationNotice.ai_status))).all()
        )
        source_counts = dict(
            (await db.execute(select(SituationNotice.source_id, func.count()).group_by(SituationNotice.source_id))).all()
        )
        risk_counts = dict(
            (
                await db.execute(
                    select(SituationAIAssessment.risk_level, func.count())
                    .where(SituationAIAssessment.status == "published")
                    .group_by(SituationAIAssessment.risk_level)
                )
            ).all()
        )
        event_counts = dict(
            (await db.execute(select(SituationEvent.status, func.count()).group_by(SituationEvent.status))).all()
        )
        latest_run = (
            await db.execute(select(SituationSourceRun).order_by(SituationSourceRun.started_at.desc()).limit(1))
        ).scalar_one_or_none()
        run_rows = []
        if latest_run is not None:
            run_rows = (
                await db.execute(select(SituationSourceRun).where(SituationSourceRun.run_id == latest_run.run_id))
            ).scalars().all()
        latest_digest = (
            await db.execute(
                select(SituationDigest)
                .where(SituationDigest.status == "published")
                .order_by(SituationDigest.period_key.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        modes = (
            await db.execute(
                select(SituationAIAssessment.author_model, SituationAIAssessment.reviewer_model)
                .where(SituationAIAssessment.status == "published")
                .order_by(SituationAIAssessment.id.desc())
                .limit(1)
            )
        ).first()
    return {
        "prompt_versions": {"notice": NOTICE_PROMPT_VERSION, "digest": DIGEST_PROMPT_VERSION},
        "notice_total": sum(int(v) for v in ai_counts.values()),
        "ai_status": {str(k): int(v) for k, v in ai_counts.items()},
        "by_source": {str(k): int(v) for k, v in source_counts.items()},
        "risk_levels": {str(k or "none"): int(v) for k, v in risk_counts.items()},
        "events": {str(k): int(v) for k, v in event_counts.items()},
        "latest_review_mode": review_mode(*modes) if modes else None,
        "latest_run": {
            "run_id": latest_run.run_id,
            "started_at": latest_run.started_at.isoformat(),
            "sources": [
                {
                    "source_id": r.source_id,
                    "status": r.status,
                    "fetched_count": r.fetched_count,
                    "new_count": r.new_count,
                    "updated_count": r.updated_count,
                    "error": r.error,
                }
                for r in run_rows
            ],
        }
        if latest_run
        else None,
        "latest_digest": {
            "period_key": latest_digest.period_key,
            "headline_en": latest_digest.headline_en,
            "headline_zh": latest_digest.headline_zh,
            "notice_count": latest_digest.notice_count,
        }
        if latest_digest
        else None,
    }


@router.get("/notices")
async def list_notices(
    response: Response,
    source_id: str | None = None,
    ai_status: str | None = None,
    disease_id: str | None = None,
    country_code: str | None = None,
    week: str | None = None,
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> list[dict]:
    query = select(SituationNotice).options(
        selectinload(SituationNotice.entities), selectinload(SituationNotice.assessments)
    )
    count = select(func.count(SituationNotice.id))
    filters = []
    if source_id:
        filters.append(SituationNotice.source_id == source_id)
    if ai_status:
        filters.append(SituationNotice.ai_status == ai_status)
    if week:
        filters.append(SituationNotice.iso_week_key == week)
    if q:
        filters.append(SituationNotice.title_en.ilike(f"%{q.strip()}%"))
    for entity_type, entity_id in (("disease", disease_id), ("country", country_code)):
        if entity_id:
            filters.append(
                SituationNotice.notice_id.in_(
                    select(SituationNoticeEntity.notice_id).where(
                        SituationNoticeEntity.entity_type == entity_type,
                        SituationNoticeEntity.entity_id == entity_id.upper(),
                        SituationNoticeEntity.active.is_(True),
                    )
                )
            )
    for clause in filters:
        query = query.where(clause)
        count = count.where(clause)
    async with get_db() as db:
        total = int((await db.execute(count)).scalar_one())
        rows = (
            await db.execute(
                query.order_by(SituationNotice.published_date.desc(), SituationNotice.notice_id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    return [_notice(row) for row in rows]


@router.get("/notices/{notice_id:path}/detail")
async def notice_detail(notice_id: str) -> dict:
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.entities), selectinload(SituationNotice.assessments))
                .where(SituationNotice.notice_id == notice_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Notice not found")
    return _notice(row, detail=True)


@router.post("/notices/{notice_id:path}/requeue")
async def requeue_notice(notice_id: str) -> dict:
    """Send one notice back through peer-reviewed AI on the next run."""
    async with get_db() as db:
        row = (await db.execute(select(SituationNotice).where(SituationNotice.notice_id == notice_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Notice not found")
        row.ai_status = "pending"
        row.ai_attempts = 0
        row.ai_next_attempt_at = None
        row.ai_last_error = None
    return {"notice_id": notice_id, "ai_status": "pending"}


@router.get("/events")
async def list_events(
    response: Response,
    status: Literal["active", "quiet"] | None = None,
    disease_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    query = select(SituationEvent).options(selectinload(SituationEvent.members))
    count = select(func.count(SituationEvent.id))
    if status:
        query = query.where(SituationEvent.status == status)
        count = count.where(SituationEvent.status == status)
    if disease_id:
        query = query.where(SituationEvent.disease_id == disease_id.upper())
        count = count.where(SituationEvent.disease_id == disease_id.upper())
    async with get_db() as db:
        total = int((await db.execute(count)).scalar_one())
        rows = (
            await db.execute(
                query.order_by(SituationEvent.last_published.desc(), SituationEvent.event_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    return [
        {
            "event_id": row.event_id,
            "disease_id": row.disease_id,
            "country_codes": row.country_codes or [],
            "first_published": row.first_published.isoformat(),
            "last_published": row.last_published.isoformat(),
            "notice_count": row.notice_count,
            "notice_ids": [m.notice_id for m in row.members],
            "source_ids": row.source_ids or [],
            "risk_level": row.risk_level,
            "status": row.status,
        }
        for row in rows
    ]


@router.get("/digests")
async def list_digests(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(52, ge=1, le=520),
) -> list[dict]:
    query = select(SituationDigest)
    count = select(func.count(SituationDigest.id))
    if status:
        query = query.where(SituationDigest.status == status)
        count = count.where(SituationDigest.status == status)
    async with get_db() as db:
        total = int((await db.execute(count)).scalar_one())
        rows = (
            await db.execute(
                query.order_by(SituationDigest.period_key.desc(), SituationDigest.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    return [
        {
            "id": row.id,
            "period_key": row.period_key,
            "status": row.status,
            "headline_en": row.headline_en,
            "headline_zh": row.headline_zh,
            "headline_fr": row.headline_fr,
            "points": row.points or [],
            "notice_count": row.notice_count,
            "prompt_version": row.prompt_version,
            "author_model": row.author_model,
            "reviewer_model": row.reviewer_model,
            "review_mode": review_mode(row.author_model, row.reviewer_model),
            "review_verdict": row.review_verdict,
            "review_score": row.review_score,
            "review_notes": row.review_notes or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/sources")
async def list_sources(history: int = Query(10, ge=1, le=100)) -> list[dict]:
    async with get_db() as db:
        runs = (
            await db.execute(
                select(SituationSourceRun).order_by(SituationSourceRun.started_at.desc()).limit(history * 10)
            )
        ).scalars().all()
        counts = dict(
            (await db.execute(select(SituationNotice.source_id, func.count()).group_by(SituationNotice.source_id))).all()
        )
    by_source: dict[str, list[SituationSourceRun]] = {}
    for run in runs:
        by_source.setdefault(run.source_id, []).append(run)
    output = []
    for source_id, registry in SOURCE_REGISTRY.items():
        rows = by_source.get(source_id, [])[:history]
        last_ok = next((r for r in rows if r.status == "ok"), None)
        output.append(
            {
                "source_id": source_id,
                "label_en": (registry.get("label") or {}).get("en"),
                "label_zh": (registry.get("label") or {}).get("zh"),
                "agency_en": (registry.get("agency") or {}).get("en"),
                "homepage": registry.get("homepage"),
                "endpoint": registry.get("endpoint"),
                "cadence": registry.get("cadence"),
                "redistribution": registry.get("redistribution"),
                "license": registry.get("license"),
                "stored_notices": int(counts.get(source_id, 0)),
                "status": rows[0].status if rows else "not_checked",
                "last_checked_at": rows[0].started_at.isoformat() if rows else None,
                "last_success_at": last_ok.started_at.isoformat() if last_ok else None,
                "last_error": rows[0].error if rows else None,
                "history": [
                    {
                        "run_id": r.run_id,
                        "started_at": r.started_at.isoformat(),
                        "status": r.status,
                        "fetched_count": r.fetched_count,
                        "new_count": r.new_count,
                        "updated_count": r.updated_count,
                        "error": r.error,
                    }
                    for r in rows
                ],
            }
        )
    return output


@router.post("/refresh", status_code=202)
async def queue_refresh(body: RefreshRequest) -> dict:
    """Queue one end-to-end Situation run on the task worker."""
    task = await task_manager.create_task(
        task_type=TaskType.REFRESH_SITUATION_SOURCES,
        task_name={
            "full": "Situation: collect, AI peer review, export",
            "no_fetch": "Situation: AI peer review and export (no fetch)",
            "metadata_only": "Situation: collect and export without AI",
        }[body.mode],
        priority=TaskPriority.HIGH,
        description="Notice-based Situation pipeline run",
        input_data={
            "mode": body.mode,
            "fetch": body.mode != "no_fetch",
            "skip_ai": body.mode == "metadata_only",
            "ai_limit": max(1, min(int(body.ai_limit), 500)),
            "digest_weeks": max(0, min(int(body.digest_weeks), 8)),
            "initiated_via": "control_panel",
        },
        tags=["situation", body.mode],
    )
    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
    return {
        "status": str(task.status.value if hasattr(task.status, "value") else task.status),
        "task_uuid": task.task_uuid,
        "mode": body.mode,
        "href": f"/operations/tasks?task={task.task_uuid}",
    }
