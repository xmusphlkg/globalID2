"""Internal review endpoints for the public Situation Room."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.situation_history_database import get_history_db
from src.core.task_manager import task_manager
from src.domain import (
    PublicHealthEvent,
    SituationOverride,
    SituationSnapshot,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.domain.situation_history import (
    SituationHistoryAudit,
    SituationHistorySignal,
    SituationHistorySnapshot,
    SituationHistorySourceCheck,
)
from src.services.data_release_service import data_release_service
from src.services.situation_events import source_adapter_registry
from src.services.situation_history_service import (
    history_health,
    record_history_audit,
    restore_history_snapshot,
    serialize_history_snapshot,
)

router = APIRouter()


def _history_is_disabled(health: dict) -> bool:
    return health.get("enabled") is False or health.get("status") == "disabled"


async def _require_history_enabled() -> dict:
    health = await history_health()
    if _history_is_disabled(health):
        raise HTTPException(503, "Situation history database is disabled")
    return health


class EventDecision(BaseModel):
    action: Literal["publish", "suppress", "merge", "correct"]
    note: str | None = Field(default=None, max_length=4000)
    actor: str | None = Field(default="dashboard", max_length=160)
    payload: dict = Field(default_factory=dict)


class SnapshotDecision(BaseModel):
    action: Literal["suppress", "correct", "rollback"]
    note: str = Field(min_length=3, max_length=4000)
    actor: str | None = Field(default="dashboard", max_length=160)
    payload: dict = Field(default_factory=dict)


class SourceRefreshRequest(BaseModel):
    mode: Literal["full", "numeric_only"] = "full"


@router.get("/overview/events")
async def candidates(
    response: Response,
    status: str = "candidate",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
) -> list[dict]:
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(PublicHealthEvent).where(
                        PublicHealthEvent.status == status
                    )
                )
            ).scalar_one()
            or 0
        )
        offset = (page - 1) * page_size
        rows = (await db.execute(
            select(PublicHealthEvent)
            .where(PublicHealthEvent.status == status)
            .order_by(PublicHealthEvent.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [
        {
            **{key: value for key, value in row.to_dict().items() if key != "id"},
            "id": row.content_hash,
        }
        for row in rows
    ]


@router.patch("/overview/events/{event_key}")
async def decide_event(event_key: str, decision: EventDecision) -> dict:
    async with get_db() as db:
        event = (
            await db.execute(
                select(PublicHealthEvent).where(PublicHealthEvent.content_hash == event_key)
            )
        ).scalar_one_or_none()
        if event is None:
            raise HTTPException(404, "Situation Room event not found")
        if decision.action == "publish":
            event.status = "published"
        elif decision.action == "suppress":
            event.status = "suppressed"
        elif decision.action == "merge":
            event.status = "merged"
        else:
            event.status = "candidate"
        event.review_note = decision.note
        db.add(SituationOverride(
            target_type="event",
            target_id=event_key,
            action=decision.action,
            note=decision.note,
            actor=decision.actor,
            payload=decision.payload,
        ))
        await db.flush()
        result = {
            **{key: value for key, value in event.to_dict().items() if key != "id"},
            "id": event.content_hash,
        }
    audit_id = await record_history_audit(
        target_type="event",
        target_id=event_key,
        action=decision.action,
        actor=decision.actor,
        note=decision.note,
        payload=decision.payload,
    )
    return {**result, "audit_id": audit_id}


@router.post("/overview/events/rebuild", status_code=202)
async def rebuild_situation() -> dict:
    """Queue the durable release pipeline instead of running in the HTTP request."""
    return await data_release_service.trigger_job(
        "site-release",
        manual=True,
        trigger="situation_dashboard",
    )


@router.get("/overview/events/health")
async def situation_health() -> dict:
    async with get_db() as db:
        latest = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().first()
        status_rows = (
            await db.execute(
                select(PublicHealthEvent.source, PublicHealthEvent.status, func.count())
                .group_by(PublicHealthEvent.source, PublicHealthEvent.status)
            )
        ).all()
        daily_gate_rows = (
            await db.execute(
                select(SituationSnapshot.period_key, SituationSnapshot.quality_gate_status)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.period_key.desc(), SituationSnapshot.revision.desc())
            )
        ).all()
    latest_gate_by_day: dict[str, str] = {}
    for period_key, gate_status in daily_gate_rows:
        latest_gate_by_day.setdefault(period_key, gate_status)
    cursor = date.fromisoformat(latest.period_key) if latest else date.today()
    consecutive_days = 0
    while latest_gate_by_day.get(cursor.isoformat()) == "passed":
        consecutive_days += 1
        cursor -= timedelta(days=1)
    release = await data_release_service.snapshot_async()
    payload = dict(latest.payload or {}) if latest else {}
    return {
        "schema_version": payload.get("schema_version"),
        "public_enabled": bool(payload.get("public_enabled", False)),
        "snapshot_id": latest.snapshot_id if latest else None,
        "checked_at": latest.checked_at if latest else None,
        "content_updated_at": latest.content_updated_at if latest else None,
        "data_through": latest.data_through if latest else None,
        "quality_gate_status": latest.quality_gate_status if latest else "missing",
        "quality_gate": latest.quality_gate if latest else {},
        "coverage": payload.get("coverage") or {},
        "analysis_execution": payload.get("analysis_execution") or {},
        "source_health": payload.get("freshness") or {},
        "section_counts": {
            name: len(payload.get(name) or [])
            for name in ("increasing", "respiratory", "emerging", "unusual")
        },
        "risk_signals": [
            {
                "id": row.get("id"),
                "disease_name": row.get("disease_name"),
                "country_name": row.get("country_name"),
                "risk": row.get("risk"),
                "detector_votes": (row.get("statistics") or {}).get("detector_votes"),
            }
            for row in (payload.get("increasing") or [])[:12]
        ],
        "event_usage": [
            {
                "id": row.get("id"),
                "source": row.get("source"),
                "title": row.get("title"),
                "disease_name": row.get("disease_name"),
                "geographies": row.get("geographies") or [],
                "usage": row.get("usage") or {},
            }
            for row in (payload.get("emerging") or [])
        ],
        "event_counts": {
            f"{source}:{status}": count for source, status, count in status_rows
        },
        "release": release,
        "history": await history_health(),
        "shadow_run": {
            "consecutive_quality_days": consecutive_days,
            "target_days": 14,
            "ready_for_review": consecutive_days >= 14,
            "started_at": (
                (cursor + timedelta(days=1)).isoformat() if consecutive_days else None
            ),
        },
    }


@router.get("/overview/events/sources")
async def situation_sources() -> list[dict]:
    """Expose configured source contracts and their operational health."""
    async with get_db() as db:
        latest = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().first()
        event_status_rows = (
            await db.execute(
                select(PublicHealthEvent.source, PublicHealthEvent.status, func.count())
                .group_by(PublicHealthEvent.source, PublicHealthEvent.status)
            )
        ).all()
    current_health = ((latest.payload or {}).get("freshness") or {}) if latest else {}
    analysis_execution = ((latest.payload or {}).get("analysis_execution") or {}) if latest else {}
    source_usage = analysis_execution.get("source_usage") or {}
    event_counts: dict[str, dict[str, int]] = {}
    for source, status, count in event_status_rows:
        event_counts.setdefault(str(source), {})[str(status)] = int(count)
    emerging = (latest.payload or {}).get("emerging") or [] if latest else []
    history = await history_health()
    source_rows = []
    if not _history_is_disabled(history):
        async with get_history_db() as history_db:
            source_rows = (
                await history_db.execute(
                    select(SituationHistorySourceCheck).order_by(
                        SituationHistorySourceCheck.checked_at.desc(),
                        SituationHistorySourceCheck.id.desc(),
                    )
                )
            ).scalars().all()
    latest_by_source: dict[str, SituationHistorySourceCheck] = {}
    last_success: dict[str, str | None] = {}
    for row in source_rows:
        latest_by_source.setdefault(row.source, row)
        if row.status in {"fresh", "stale"} and row.source not in last_success:
            last_success[row.source] = row.checked_at
    def adapter_health(health_key: str) -> dict:
        current = current_health.get(health_key)
        if current:
            return current
        historical = latest_by_source.get(health_key)
        if historical is None:
            return {"status": "not_checked"}
        return {**(historical.details or {}), "status": historical.status, "from_history": True}
    return [
        {
            **adapter,
            "health": adapter_health(adapter["health_key"]),
            "usage": (
                {
                    "mode": "official_event",
                    "persisted": event_counts.get(adapter["source_id"], {}),
                    "in_latest_emerging": sum(
                        1 for event in emerging if event.get("source") == adapter["source_id"]
                    ),
                    "used_in_composite_risk": sum(
                        1
                        for event in emerging
                        if event.get("source") == adapter["source_id"]
                        and (event.get("usage") or {}).get("status") == "used_in_composite_risk"
                    ),
                }
                if adapter["source_kind"] == "official_event"
                else {
                    "mode": "numeric_series" if adapter.get("analysis_source_system") else "context_only",
                    **(
                        source_usage.get(adapter.get("analysis_source_system")) or {}
                        if adapter.get("analysis_source_system")
                        else {}
                    ),
                    "not_analyzed_reason": (
                        "Categorical all-respiratory context is not disease-specific numeric evidence"
                        if not adapter.get("analysis_source_system")
                        else None
                    ),
                }
            ),
            "last_success_at": last_success.get(adapter["health_key"]),
            "latest_snapshot_id": latest.snapshot_id if latest else None,
        }
        for adapter in source_adapter_registry()
    ]


@router.get("/overview/events/analysis")
async def situation_analysis_ledger(
    response: Response,
    status: Literal["analyzed", "rejected"] | None = None,
    source_system: str | None = None,
    rejection_reason: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> list[dict]:
    """Return the complete per-series execution/rejection ledger."""
    async with get_db() as db:
        latest = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().first()
    rows = list(((latest.payload or {}).get("_analysis_ledger") or []) if latest else [])
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if source_system:
        needle = source_system.strip().casefold()
        rows = [row for row in rows if needle in str(row.get("source_system") or "").casefold()]
    if rejection_reason:
        rows = [row for row in rows if row.get("rejection_reason") == rejection_reason]
    total = len(rows)
    offset = (page - 1) * page_size
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return rows[offset : offset + page_size]


@router.post("/overview/events/sources/refresh", status_code=202)
async def queue_situation_source_refresh(body: SourceRefreshRequest) -> dict:
    """Queue source acquisition separately from static-site deployment."""
    fetch_events = body.mode == "full"
    task = await task_manager.create_task(
        task_type=TaskType.REFRESH_SITUATION_SOURCES,
        task_name=(
            "Situation Room source acquisition"
            if fetch_events
            else "Situation Room numerical signal recalculation"
        ),
        priority=TaskPriority.HIGH,
        description=(
            "Fetch official event and CDC adapters, recalculate signals, run quality gates, and archive revisions."
            if fetch_events
            else "Recalculate source-native numerical signals without external HTTP requests."
        ),
        input_data={
            "fetch_events": fetch_events,
            "mode": body.mode,
            "initiated_via": "sources_control_panel",
        },
        tags=["situation-room", "source-acquisition", body.mode],
    )
    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
    return {
        "status": str(task.status.value if hasattr(task.status, "value") else task.status),
        "task_uuid": task.task_uuid,
        "mode": body.mode,
        "href": f"/operations/tasks?task={task.task_uuid}",
    }


@router.get("/overview/events/snapshots")
async def runs(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> list[dict]:
    async with get_db() as db:
        total = int(
            (await db.execute(select(func.count()).select_from(SituationSnapshot))).scalar_one()
            or 0
        )
        offset = (page - 1) * page_size
        rows = (await db.execute(
            select(SituationSnapshot)
            .order_by(SituationSnapshot.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [{
        "snapshot_id": row.snapshot_id,
        "snapshot_kind": row.snapshot_kind,
        "period_key": row.period_key,
        "iso_week": row.iso_week,
        "generated_at": row.generated_at,
        "checked_at": row.checked_at,
        "content_updated_at": row.content_updated_at,
        "data_through": row.data_through,
        "method_version": row.method_version,
        "input_hash": row.input_hash,
        "status": row.status,
        "quality_gate_status": row.quality_gate_status,
        "quality_gate": row.quality_gate,
        "revision": row.revision,
        "supersedes_snapshot_id": row.supersedes_snapshot_id,
        "coverage": (row.payload or {}).get("coverage") or {},
    } for row in rows]


@router.patch("/overview/events/snapshots/{snapshot_id}")
async def decide_snapshot(snapshot_id: str, decision: SnapshotDecision) -> dict:
    if decision.action == "rollback":
        try:
            return await restore_history_snapshot(
                snapshot_id, actor=decision.actor, note=decision.note
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    async with get_db() as db:
        snapshot = (
            await db.execute(
                select(SituationSnapshot).where(SituationSnapshot.snapshot_id == snapshot_id)
            )
        ).scalar_one_or_none()
        if snapshot is None:
            raise HTTPException(404, "Situation Room snapshot not found")
        if decision.action == "suppress":
            snapshot.status = "suppressed"
        else:
            snapshot.status = "correction_requested"
        db.add(
            SituationOverride(
                target_type="snapshot",
                target_id=snapshot_id,
                action=decision.action,
                note=decision.note,
                actor=decision.actor,
                payload=decision.payload,
            )
        )
        await db.flush()
        result = {
            "snapshot_id": snapshot.snapshot_id,
            "status": snapshot.status,
            "period_key": snapshot.period_key,
            "revision": snapshot.revision,
        }
    audit_id = await record_history_audit(
        target_type="snapshot",
        target_id=snapshot_id,
        action=decision.action,
        actor=decision.actor,
        note=decision.note,
        payload=decision.payload,
    )
    return {**result, "audit_id": audit_id}


@router.post("/overview/events/history/sync", status_code=202)
async def queue_history_sync() -> dict:
    """Queue a durable, worker-executed history reconciliation."""
    task = await task_manager.create_task(
        task_type=TaskType.SYNC_SITUATION_HISTORY,
        task_name="Situation Room history reconciliation",
        priority=TaskPriority.NORMAL,
        description="Reconcile all primary Situation snapshots into the dedicated history database.",
        input_data={"mode": "dashboard_reconcile", "initiated_via": "dashboard"},
        tags=["situation-room", "history", "reconcile"],
    )
    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
    return {
        "status": str(task.status.value if hasattr(task.status, "value") else task.status),
        "task_uuid": task.task_uuid,
        "href": f"/operations/tasks?task={task.task_uuid}",
    }


@router.get("/overview/events/history/health")
async def get_history_health() -> dict:
    return await history_health()


@router.get("/overview/events/history/snapshots")
async def history_snapshots(
    response: Response,
    snapshot_kind: str | None = Query(default=None, pattern="^(daily|weekly|monthly)$"),
    period_key: str | None = Query(default=None, max_length=20),
    disease: str | None = Query(default=None, max_length=240),
    country: str | None = Query(default=None, max_length=240),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> list[dict]:
    await _require_history_enabled()  # Initializes a newly configured empty schema safely.
    query = select(SituationHistorySnapshot).options(
        selectinload(SituationHistorySnapshot.signals)
    )
    if snapshot_kind:
        query = query.where(SituationHistorySnapshot.snapshot_kind == snapshot_kind)
    if period_key:
        query = query.where(SituationHistorySnapshot.period_key == period_key)
    if disease or country:
        query = query.join(SituationHistorySignal)
        if disease:
            token = f"%{disease.strip()}%"
            query = query.where(
                or_(
                    SituationHistorySignal.disease_id.ilike(token),
                    SituationHistorySignal.disease_name.ilike(token),
                )
            )
        if country:
            token = f"%{country.strip()}%"
            query = query.where(
                or_(
                    SituationHistorySignal.country_code.ilike(token),
                    SituationHistorySignal.country_name.ilike(token),
                )
            )
        query = query.distinct()
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    offset = (page - 1) * page_size
    async with get_history_db() as db:
        total = int((await db.execute(count_query)).scalar_one() or 0)
        rows = (
            await db.execute(
                query.order_by(
                    SituationHistorySnapshot.checked_at.desc(),
                    SituationHistorySnapshot.snapshot_kind,
                    SituationHistorySnapshot.revision.desc(),
                )
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().unique().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [serialize_history_snapshot(row) for row in rows]


@router.get("/overview/events/history/snapshots/{snapshot_id}")
async def history_snapshot_detail(snapshot_id: str) -> dict:
    await _require_history_enabled()
    async with get_history_db() as db:
        row = (
            await db.execute(
                select(SituationHistorySnapshot)
                .options(
                    selectinload(SituationHistorySnapshot.signals),
                    selectinload(SituationHistorySnapshot.source_checks),
                )
                .where(SituationHistorySnapshot.snapshot_id == snapshot_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Situation history snapshot not found")
        audits = (
            await db.execute(
                select(SituationHistoryAudit)
                .where(
                    SituationHistoryAudit.target_type == "snapshot",
                    SituationHistoryAudit.target_id == snapshot_id,
                )
                .order_by(SituationHistoryAudit.happened_at.desc())
            )
        ).scalars().all()
        result = serialize_history_snapshot(row, include_payload=True)
        result["signals"] = [
            {
                "signal_id": signal.signal_id,
                "section": signal.section,
                "disease_id": signal.disease_id,
                "disease_name": signal.disease_name,
                "country_code": signal.country_code,
                "country_name": signal.country_name,
                "series_id": signal.series_id,
                "metric_type": signal.metric_type,
                "cadence": signal.cadence,
                "change_pct": signal.change_pct,
                "standard_z": signal.standard_z,
                "robust_z": signal.robust_z,
                "ewma_alarm": signal.ewma_alarm,
                "bayesian_change_probability": signal.bayesian_change_probability,
                "detector_votes": signal.detector_votes,
                "risk_score": signal.risk_score,
                "risk_level": signal.risk_level,
                "risk_confidence": signal.risk_confidence,
                "payload": signal.payload,
            }
            for signal in row.signals
        ]
        result["source_checks"] = [
            {
                "source": check.source,
                "status": check.status,
                "checked_at": check.checked_at,
                "item_count": check.item_count,
                "stale_until": check.stale_until,
                "error": check.error,
                "details": check.details,
            }
            for check in sorted(row.source_checks, key=lambda item: item.checked_at or "", reverse=True)
        ]
        result["audit"] = [
            {
                "audit_id": audit.audit_id,
                "action": audit.action,
                "actor": audit.actor,
                "note": audit.note,
                "payload": audit.payload,
                "happened_at": audit.happened_at,
            }
            for audit in audits
        ]
        return result


@router.get("/overview/events/history/compare")
async def compare_history_snapshots(
    left: str = Query(min_length=1, max_length=120),
    right: str = Query(min_length=1, max_length=120),
) -> dict:
    await _require_history_enabled()
    async with get_history_db() as db:
        rows = (
            await db.execute(
                select(SituationHistorySnapshot)
                .options(selectinload(SituationHistorySnapshot.signals))
                .where(SituationHistorySnapshot.snapshot_id.in_([left, right]))
            )
        ).scalars().unique().all()
    by_id = {row.snapshot_id: row for row in rows}
    if left not in by_id or right not in by_id:
        raise HTTPException(404, "One or both Situation history snapshots were not found")
    left_signals = {(item.section, item.signal_id): item for item in by_id[left].signals}
    right_signals = {(item.section, item.signal_id): item for item in by_id[right].signals}
    changes = []
    for key in sorted(set(left_signals) | set(right_signals)):
        before = left_signals.get(key)
        after = right_signals.get(key)
        if before is None:
            status = "added"
        elif after is None:
            status = "removed"
        else:
            comparable = (
                before.change_pct,
                before.standard_z,
                before.detector_votes,
                before.risk_score,
                before.risk_level,
            )
            updated = (
                after.change_pct,
                after.standard_z,
                after.detector_votes,
                after.risk_score,
                after.risk_level,
            )
            status = "changed" if comparable != updated else "unchanged"
        if status == "unchanged":
            continue
        reference = after or before
        changes.append(
            {
                "section": key[0],
                "signal_id": key[1],
                "status": status,
                "disease_name": reference.disease_name,
                "country_name": reference.country_name,
                "before": (
                    {
                        "change_pct": before.change_pct,
                        "standard_z": before.standard_z,
                        "detector_votes": before.detector_votes,
                        "risk_score": before.risk_score,
                        "risk_level": before.risk_level,
                    }
                    if before
                    else None
                ),
                "after": (
                    {
                        "change_pct": after.change_pct,
                        "standard_z": after.standard_z,
                        "detector_votes": after.detector_votes,
                        "risk_score": after.risk_score,
                        "risk_level": after.risk_level,
                    }
                    if after
                    else None
                ),
            }
        )
    return {
        "left": serialize_history_snapshot(by_id[left]),
        "right": serialize_history_snapshot(by_id[right]),
        "summary": {
            "added": sum(item["status"] == "added" for item in changes),
            "removed": sum(item["status"] == "removed" for item in changes),
            "changed": sum(item["status"] == "changed" for item in changes),
        },
        "changes": changes,
    }


@router.get("/overview/events/history/audit")
async def history_audit(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)
) -> list[dict]:
    await _require_history_enabled()
    async with get_history_db() as db:
        rows = (
            await db.execute(
                select(SituationHistoryAudit)
                .order_by(SituationHistoryAudit.happened_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    return [
        {
            "audit_id": row.audit_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "action": row.action,
            "actor": row.actor,
            "note": row.note,
            "payload": row.payload,
            "happened_at": row.happened_at,
        }
        for row in rows
    ]
