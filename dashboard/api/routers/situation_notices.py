"""Control-plane endpoints for the alert-based Situation module.

Read views over reports, their documents, peer-reviewed extractions, alerts and
outbreaks, plus two operational actions: queue a pipeline run and re-queue one
report. Publication itself has no human gate; the peer review recorded on each
extraction is the decision.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.task_manager import task_manager
from src.domain import (
    SituationAlert,
    SituationDocument,
    SituationExtraction,
    SituationNotice,
    SituationOutbreak,
    SituationSourceRun,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.services.global_sources.catalogue import SOURCE_REGISTRY
from src.services.situation_notices.ai import EXTRACTION_PROMPT_VERSION, review_mode
from src.services.situation_notices.source_control import (
    normalize_situation_sources,
    reject_overlapping_run,
    source_monitors,
)
from src.services.situation_notices.source_log import log_situation_event

router = APIRouter(prefix="/situation")


class RefreshRequest(BaseModel):
    mode: Literal["full", "no_fetch", "metadata_only"] = "full"
    ai_limit: int = 30
    #: Empty collects every registered feed. Names limit the run to those feeds.
    sources: list[str] = Field(default_factory=list)


def _published(row: SituationNotice) -> SituationExtraction | None:
    return next((item for item in row.extractions if item.status == "published"), None)


def _extraction(row: SituationExtraction) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "prompt_version": row.prompt_version,
        "document_hash": row.document_hash,
        "document_type": row.document_type,
        "input_mode": row.input_mode,
        "alert_count": row.alert_count,
        "author_model": row.author_model,
        "reviewer_model": row.reviewer_model,
        "review_mode": review_mode(row.author_model, row.reviewer_model),
        "review_verdict": row.review_verdict,
        "review_rounds": row.review_rounds,
        "review_score": row.review_score,
        "review_notes": row.review_notes or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _alert(row: SituationAlert) -> dict:
    return {
        "alert_id": row.alert_id,
        "notice_id": row.notice_id,
        "outbreak_id": row.outbreak_id,
        "disease_id": row.disease_id,
        "disease_label": row.disease_label,
        "country_code": row.country_code,
        "admin_area": row.admin_area,
        "status": row.status,
        "cases": row.cases,
        "deaths": row.deaths,
        "count_basis": row.count_basis,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "quote": row.quote,
        "summary_en": row.summary_en,
        "published_date": row.published_date.isoformat(),
        "source_id": row.source_id,
    }


def _document(row: SituationDocument | None) -> dict | None:
    if row is None:
        return None
    return {
        "status": row.status,
        "media_type": row.media_type,
        "byte_size": row.byte_size,
        "page_count": row.page_count,
        "text_chars": len(row.text) if row.text else 0,
        "has_original": bool(row.cache_path),
        "attempts": row.attempts,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "next_attempt_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "error": row.error,
    }


def _notice(row: SituationNotice, *, detail: bool = False) -> dict:
    published = _published(row)
    payload = {
        "notice_id": row.notice_id,
        "source_id": row.source_id,
        "source_label": (SOURCE_REGISTRY.get(row.source_id, {}).get("label") or {}).get("en"),
        "record_type": row.record_type,
        "title_en": row.title_en,
        "url": row.url,
        "published_date": row.published_date.isoformat(),
        "iso_week_key": row.iso_week_key,
        "redistribution": row.redistribution,
        "ai_status": row.ai_status,
        "ai_attempts": row.ai_attempts,
        "ai_last_error": row.ai_last_error,
        "ai_next_attempt_at": row.ai_next_attempt_at.isoformat() if row.ai_next_attempt_at else None,
        "document": _document(row.document),
        "document_type": published.document_type if published else None,
        "input_mode": published.input_mode if published else None,
        "alert_count": published.alert_count if published else 0,
        "review_mode": review_mode(published.author_model, published.reviewer_model) if published else None,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }
    if detail:
        payload["summary_en"] = row.summary_en
        payload["content_hash"] = row.content_hash
        payload["extractions"] = [_extraction(item) for item in sorted(row.extractions, key=lambda i: i.id or 0, reverse=True)]
        payload["alerts"] = [_alert(item) for item in row.alerts]
    return payload


@router.get("/overview")
async def situation_overview() -> dict:
    async with get_db() as db:
        ai_counts = dict(
            (await db.execute(select(SituationNotice.ai_status, func.count()).group_by(SituationNotice.ai_status))).all()
        )
        document_counts = dict(
            (await db.execute(select(SituationDocument.status, func.count()).group_by(SituationDocument.status))).all()
        )
        source_counts = dict(
            (await db.execute(select(SituationNotice.source_id, func.count()).group_by(SituationNotice.source_id))).all()
        )
        status_counts = dict(
            (await db.execute(select(SituationAlert.status, func.count()).group_by(SituationAlert.status))).all()
        )
        input_modes = dict(
            (
                await db.execute(
                    select(SituationExtraction.input_mode, func.count())
                    .where(SituationExtraction.status == "published")
                    .group_by(SituationExtraction.input_mode)
                )
            ).all()
        )
        outbreak_counts = dict(
            (await db.execute(select(SituationOutbreak.status, func.count()).group_by(SituationOutbreak.status))).all()
        )
        latest_run = (
            await db.execute(select(SituationSourceRun).order_by(SituationSourceRun.started_at.desc()).limit(1))
        ).scalar_one_or_none()
        run_rows = []
        if latest_run is not None:
            run_rows = (
                await db.execute(select(SituationSourceRun).where(SituationSourceRun.run_id == latest_run.run_id))
            ).scalars().all()
        modes = (
            await db.execute(
                select(SituationExtraction.author_model, SituationExtraction.reviewer_model)
                .where(SituationExtraction.status == "published")
                .order_by(SituationExtraction.id.desc())
                .limit(1)
            )
        ).first()
    return {
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "notice_total": sum(int(v) for v in ai_counts.values()),
        "ai_status": {str(k): int(v) for k, v in ai_counts.items()},
        "document_status": {str(k): int(v) for k, v in document_counts.items()},
        "by_source": {str(k): int(v) for k, v in source_counts.items()},
        "alert_status": {str(k): int(v) for k, v in status_counts.items()},
        "input_modes": {str(k or "unknown"): int(v) for k, v in input_modes.items()},
        "outbreaks": {str(k): int(v) for k, v in outbreak_counts.items()},
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
    }


@router.get("/notices")
async def list_notices(
    response: Response,
    source_id: str | None = None,
    ai_status: str | None = None,
    week: str | None = None,
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> list[dict]:
    query = select(SituationNotice).options(
        selectinload(SituationNotice.document), selectinload(SituationNotice.extractions)
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
                .options(
                    selectinload(SituationNotice.document),
                    selectinload(SituationNotice.extractions),
                    selectinload(SituationNotice.alerts),
                )
                .where(SituationNotice.notice_id == notice_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Notice not found")
    return _notice(row, detail=True)


@router.post("/notices/{notice_id:path}/requeue")
async def requeue_notice(notice_id: str) -> dict:
    """Send one report back through document fetch and peer-reviewed extraction on the next run."""
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationNotice)
                .options(selectinload(SituationNotice.document))
                .where(SituationNotice.notice_id == notice_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Notice not found")
        row.ai_status = "pending"
        row.ai_attempts = 0
        row.ai_next_attempt_at = None
        row.ai_last_error = None
        if row.document is not None and row.document.status in ("failed", "unavailable"):
            row.document.status = "pending"
            row.document.attempts = 0
            row.document.next_attempt_at = None
            row.document.error = None
    return {"notice_id": notice_id, "ai_status": "pending"}


@router.get("/alerts")
async def list_alerts(
    response: Response,
    country_code: str | None = Query(None, min_length=2, max_length=2),
    disease_id: str | None = None,
    status: str | None = None,
    outbreak_id: str | None = None,
    source_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> list[dict]:
    query = select(SituationAlert)
    count = select(func.count(SituationAlert.id))
    filters = []
    if country_code:
        filters.append(SituationAlert.country_code == country_code.upper())
    if disease_id:
        filters.append(SituationAlert.disease_id == disease_id.upper())
    if status:
        filters.append(SituationAlert.status == status)
    if outbreak_id:
        filters.append(SituationAlert.outbreak_id == outbreak_id)
    if source_id:
        filters.append(SituationAlert.source_id == source_id)
    for clause in filters:
        query = query.where(clause)
        count = count.where(clause)
    async with get_db() as db:
        total = int((await db.execute(count)).scalar_one())
        rows = (
            await db.execute(
                query.order_by(SituationAlert.published_date.desc(), SituationAlert.alert_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    return [_alert(row) for row in rows]


@router.get("/outbreaks")
async def list_outbreaks(
    response: Response,
    status: Literal["active", "quiet"] | None = None,
    disease_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
) -> list[dict]:
    query = select(SituationOutbreak)
    count = select(func.count(SituationOutbreak.id))
    if status:
        query = query.where(SituationOutbreak.status == status)
        count = count.where(SituationOutbreak.status == status)
    if disease_id:
        query = query.where(SituationOutbreak.disease_id == disease_id.upper())
        count = count.where(SituationOutbreak.disease_id == disease_id.upper())
    async with get_db() as db:
        total = int((await db.execute(count)).scalar_one())
        rows = (
            await db.execute(
                query.order_by(SituationOutbreak.last_published.desc(), SituationOutbreak.outbreak_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    return [
        {
            "outbreak_id": row.outbreak_id,
            "disease_id": row.disease_id,
            "disease_label": row.disease_label,
            "country_codes": row.country_codes or [],
            "first_published": row.first_published.isoformat(),
            "last_published": row.last_published.isoformat(),
            "alert_count": row.alert_count,
            "notice_count": row.notice_count,
            "source_ids": row.source_ids or [],
            "status": row.status,
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
        monitors = await source_monitors(db)
    by_source: dict[str, list[SituationSourceRun]] = {}
    for run in runs:
        by_source.setdefault(run.source_id, []).append(run)
    output = []
    for source_id, registry in SOURCE_REGISTRY.items():
        rows = by_source.get(source_id, [])[:history]
        last_ok = next((r for r in rows if r.status == "ok"), None)
        monitor = monitors.get(source_id) or {}
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
                "latest_task_uuid": monitor.get("latest_task_uuid"),
                "latest_task_status": monitor.get("latest_task_status"),
                "stages": monitor.get("stages") or [],
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
    """Queue one Situation run, optionally limited to named feeds."""
    try:
        selected = normalize_situation_sources(body.sources)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with get_db() as db:
        await reject_overlapping_run(db, selected)
    base_name = {
        "full": "Situation: collect, read reports, extract alerts, export",
        "no_fetch": "Situation: read reports, extract alerts, export (no fetch)",
        "metadata_only": "Situation: collect and export without AI",
    }[body.mode]
    if len(selected) == 1:
        label = (SOURCE_REGISTRY[selected[0]].get("label") or {}).get("en") or selected[0]
        task_name = f"{base_name} · {label}"
    elif selected:
        task_name = f"{base_name} · {len(selected)} sources"
    else:
        task_name = base_name
    task = await task_manager.create_task(
        task_type=TaskType.REFRESH_SITUATION_SOURCES,
        task_name=task_name,
        priority=TaskPriority.HIGH,
        description="Alert-based Situation pipeline run",
        input_data={
            "mode": body.mode,
            "fetch": body.mode != "no_fetch",
            "skip_ai": body.mode == "metadata_only",
            "ai_limit": max(1, min(int(body.ai_limit), 200)),
            "initiated_via": "control_panel",
            **({"sources": selected} if selected else {}),
        },
        tags=["situation", body.mode, *selected],
    )
    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
    scope = ", ".join(selected) if selected else "all registered feeds"
    await log_situation_event(
        task.task_uuid,
        title="Situation collection queued",
        content=f"Queued {body.mode} collection for {scope}.",
        metadata={"event": "situation_queued", "mode": body.mode, "sources": selected},
    )
    return {
        "status": str(task.status.value if hasattr(task.status, "value") else task.status),
        "task_uuid": task.task_uuid,
        "mode": body.mode,
        "sources": selected,
        "href": f"/operations/tasks?task={task.task_uuid}",
    }
