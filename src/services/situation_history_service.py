"""Archival, reconciliation, search, and restore operations for Situation history."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.config import get_config
from src.core.database import get_db
from src.core.situation_history_database import (
    get_history_db,
    history_database_descriptor,
    init_history_database,
)
from src.domain import SituationOverride, SituationSnapshot
from src.domain.situation_history import (
    SituationHistoryAudit,
    SituationHistorySignal,
    SituationHistorySnapshot,
    SituationHistorySourceCheck,
    SituationHistorySyncRun,
)


SECTIONS = ("increasing", "respiratory", "emerging", "unusual")


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_evidence_url(item: dict[str, Any]) -> str | None:
    if item.get("source_url"):
        return str(item["source_url"])
    for evidence in item.get("evidence_links") or []:
        if isinstance(evidence, str):
            return evidence
        if isinstance(evidence, dict) and evidence.get("url"):
            return str(evidence["url"])
    metrics = item.get("metrics") or []
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("source_url"):
            return str(metric["source_url"])
    return None


def _signal_record(
    history_snapshot_id: int,
    section: str,
    item: dict[str, Any],
    default_data_through: str | None,
) -> SituationHistorySignal:
    statistics = item.get("statistics") or {}
    risk = item.get("risk") or {}
    window = item.get("window") or {}
    geographies = item.get("geographies") or []
    geography = {
        "key": item.get("geography_key"),
        "country_code": item.get("country_code"),
        "country_name": item.get("country_name"),
        "geographies": geographies,
    }
    source_id = item.get("source_system") or item.get("source")
    return SituationHistorySignal(
        history_snapshot_id=history_snapshot_id,
        signal_id=str(item.get("id") or f"{section}:{item.get('disease_id')}:{item.get('country_code')}"),
        section=section,
        disease_id=item.get("disease_id"),
        disease_name=item.get("disease_name"),
        country_code=item.get("country_code"),
        country_name=item.get("country_name"),
        geography=geography,
        series_id=item.get("series_code"),
        source_id=str(source_id) if source_id else None,
        metric_type=item.get("metric_type"),
        unit=item.get("unit"),
        cadence=item.get("cadence"),
        comparison_window=window.get("label"),
        current_value=_finite(window.get("current")),
        previous_value=_finite(window.get("previous")),
        change_pct=_finite(window.get("change_pct")),
        standard_z=_finite(statistics.get("z_score")),
        robust_z=_finite(statistics.get("robust_z")),
        ewma_value=_finite(statistics.get("ewma_residual")),
        ewma_alarm=(
            int(bool((statistics.get("detectors") or {}).get("ewma")))
            if statistics
            else None
        ),
        bayesian_change_probability=_finite(statistics.get("bayesian_change_probability")),
        detector_votes=(
            int(statistics["detector_votes"])
            if statistics.get("detector_votes") is not None
            else None
        ),
        risk_score=_finite(risk.get("score")),
        risk_level=risk.get("level"),
        risk_confidence=risk.get("confidence"),
        risk_dimensions=risk.get("dimensions") or {},
        missing_dimensions=risk.get("missing_dimensions") or [],
        data_through=item.get("data_through") or default_data_through,
        evidence_url=_first_evidence_url(item),
        payload=item,
    )


async def _archive_in_session(db, snapshot: SituationSnapshot) -> dict[str, int]:
    existing = (
        await db.execute(
            select(SituationHistorySnapshot)
            .options(selectinload(SituationHistorySnapshot.signals))
            .where(SituationHistorySnapshot.snapshot_id == snapshot.snapshot_id)
        )
    ).scalar_one_or_none()
    payload = dict(snapshot.payload or {})
    inserted_snapshot = 0
    is_new = existing is None
    if is_new:
        existing = SituationHistorySnapshot(
            snapshot_id=snapshot.snapshot_id,
            primary_snapshot_id=snapshot.id,
            snapshot_kind=snapshot.snapshot_kind,
            period_key=snapshot.period_key,
            revision=snapshot.revision,
            supersedes_snapshot_id=snapshot.supersedes_snapshot_id,
            generated_at=snapshot.generated_at,
            checked_at=snapshot.checked_at,
            content_updated_at=snapshot.content_updated_at,
            data_through=snapshot.data_through,
            method_version=snapshot.method_version,
            input_hash=snapshot.input_hash,
            operational_status=snapshot.status,
            quality_gate_status=snapshot.quality_gate_status,
            quality_gate=snapshot.quality_gate or {},
            coverage=payload.get("coverage") or {},
            payload=payload,
        )
        db.add(existing)
        await db.flush()
        inserted_snapshot = 1
    else:
        # Content fields are revision-immutable. Only check/operational metadata
        # is advanced when identical inputs are observed again.
        existing.primary_snapshot_id = snapshot.id
        existing.checked_at = snapshot.checked_at
        existing.operational_status = snapshot.status
        existing.quality_gate_status = snapshot.quality_gate_status
        existing.quality_gate = snapshot.quality_gate or {}

    signal_count = 0
    if is_new or not existing.signals:
        for section in SECTIONS:
            for item in payload.get(section) or []:
                if not isinstance(item, dict):
                    continue
                db.add(_signal_record(existing.id, section, item, snapshot.data_through))
                signal_count += 1

    existing_checks = {
        (source, checked_at)
        for source, checked_at in (
            await db.execute(
                select(
                    SituationHistorySourceCheck.source,
                    SituationHistorySourceCheck.checked_at,
                ).where(SituationHistorySourceCheck.history_snapshot_id == existing.id)
            )
        ).all()
    }
    source_count = 0
    for source, details in (payload.get("freshness") or {}).items():
        if not isinstance(details, dict):
            details = {"status": str(details)}
        source_checked_at = str(details.get("checked_at") or snapshot.checked_at)
        if (source, source_checked_at) in existing_checks:
            continue
        db.add(
            SituationHistorySourceCheck(
                history_snapshot_id=existing.id,
                source=str(source),
                status=str(details.get("status") or "unknown"),
                checked_at=source_checked_at,
                item_count=(int(details["item_count"]) if details.get("item_count") is not None else None),
                stale_until=details.get("stale_until"),
                error=details.get("error"),
                details=details,
            )
        )
        source_count += 1
    return {
        "snapshots_written": inserted_snapshot,
        "signals_written": signal_count,
        "source_checks_written": source_count,
    }


async def archive_snapshot(snapshot: SituationSnapshot) -> dict[str, int]:
    """Idempotently archive one primary snapshot and its evidence."""
    if not get_config().situation_history_database.enabled:
        return {"snapshots_written": 0, "signals_written": 0, "source_checks_written": 0}
    await init_history_database(create_database=True)
    async with get_history_db() as history_db:
        return await _archive_in_session(history_db, snapshot)


async def sync_history(*, mode: str = "reconcile") -> dict[str, Any]:
    """Backfill every primary snapshot into the isolated history database."""
    await init_history_database(create_database=True)
    async with get_history_db() as history_db:
        run = SituationHistorySyncRun(mode=mode, status="running")
        history_db.add(run)
        await history_db.flush()
        run_id = run.run_id

    totals = {
        "snapshots_seen": 0,
        "snapshots_written": 0,
        "signals_written": 0,
        "source_checks_written": 0,
    }
    try:
        async with get_db() as primary_db:
            snapshots = (
                await primary_db.execute(
                    select(SituationSnapshot).order_by(
                        SituationSnapshot.created_at.asc(), SituationSnapshot.id.asc()
                    )
                )
            ).scalars().all()
        totals["snapshots_seen"] = len(snapshots)
        async with get_history_db() as history_db:
            for snapshot in snapshots:
                counts = await _archive_in_session(history_db, snapshot)
                for key, value in counts.items():
                    totals[key] += value
            run = (
                await history_db.execute(
                    select(SituationHistorySyncRun).where(SituationHistorySyncRun.run_id == run_id)
                )
            ).scalar_one()
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            for key, value in totals.items():
                setattr(run, key, value)
            run.details = {"database": history_database_descriptor().get("database")}
    except Exception as exc:
        async with get_history_db() as history_db:
            run = (
                await history_db.execute(
                    select(SituationHistorySyncRun).where(SituationHistorySyncRun.run_id == run_id)
                )
            ).scalar_one()
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error = str(exc)[:4000]
            for key, value in totals.items():
                setattr(run, key, value)
        raise
    return {"run_id": run_id, "status": "completed", **totals}


async def record_history_audit(
    *,
    target_type: str,
    target_id: str,
    action: str,
    actor: str | None,
    note: str | None,
    payload: dict[str, Any] | None = None,
) -> str:
    await init_history_database(create_database=True)
    async with get_history_db() as history_db:
        row = SituationHistoryAudit(
            target_type=target_type,
            target_id=target_id,
            action=action,
            actor=actor,
            note=note,
            payload=payload or {},
        )
        history_db.add(row)
        await history_db.flush()
        return row.audit_id


async def history_health() -> dict[str, Any]:
    """Control-plane-safe database health and inventory."""
    descriptor = history_database_descriptor()
    if not descriptor["enabled"]:
        return {**descriptor, "status": "disabled"}
    try:
        await init_history_database()
        async with get_history_db() as db:
            snapshot_count = int(
                (await db.execute(select(func.count()).select_from(SituationHistorySnapshot))).scalar_one()
            )
            signal_count = int(
                (await db.execute(select(func.count()).select_from(SituationHistorySignal))).scalar_one()
            )
            source_check_count = int(
                (await db.execute(select(func.count()).select_from(SituationHistorySourceCheck))).scalar_one()
            )
            audit_count = int(
                (await db.execute(select(func.count()).select_from(SituationHistoryAudit))).scalar_one()
            )
            latest = (
                await db.execute(
                    select(SituationHistorySnapshot).order_by(
                        SituationHistorySnapshot.checked_at.desc(),
                        SituationHistorySnapshot.revision.desc(),
                    )
                )
            ).scalars().first()
            last_sync = (
                await db.execute(
                    select(SituationHistorySyncRun).order_by(SituationHistorySyncRun.started_at.desc())
                )
            ).scalars().first()
            size_bytes = None
            if str(descriptor.get("driver", "")).startswith("postgresql"):
                size_bytes = int(
                    (await db.execute(select(func.pg_database_size(func.current_database())))).scalar_one()
                )
        return {
            **descriptor,
            "status": "healthy",
            "size_bytes": size_bytes,
            "snapshot_count": snapshot_count,
            "signal_count": signal_count,
            "source_check_count": source_check_count,
            "audit_count": audit_count,
            "latest_snapshot_id": latest.snapshot_id if latest else None,
            "latest_checked_at": latest.checked_at if latest else None,
            "last_sync": (
                {
                    "run_id": last_sync.run_id,
                    "mode": last_sync.mode,
                    "status": last_sync.status,
                    "started_at": last_sync.started_at,
                    "finished_at": last_sync.finished_at,
                    "snapshots_seen": last_sync.snapshots_seen,
                    "snapshots_written": last_sync.snapshots_written,
                    "error": last_sync.error,
                }
                if last_sync
                else None
            ),
        }
    except Exception as exc:
        return {**descriptor, "status": "unavailable", "error": str(exc)[:1000]}


async def restore_history_snapshot(
    snapshot_id: str, *, actor: str | None, note: str
) -> dict[str, Any]:
    """Restore a gate-passed historical revision to primary operational state."""
    await init_history_database(create_database=True)
    async with get_history_db() as history_db:
        historical = (
            await history_db.execute(
                select(SituationHistorySnapshot).where(
                    SituationHistorySnapshot.snapshot_id == snapshot_id
                )
            )
        ).scalar_one_or_none()
        if historical is None:
            raise LookupError("Situation history snapshot not found")
        if historical.quality_gate_status != "passed":
            raise ValueError("Only a quality-gate-passed revision can be restored")
        historical_values = {
            column: getattr(historical, column)
            for column in (
                "snapshot_id",
                "snapshot_kind",
                "period_key",
                "generated_at",
                "checked_at",
                "content_updated_at",
                "data_through",
                "method_version",
                "input_hash",
                "quality_gate_status",
                "quality_gate",
                "revision",
                "supersedes_snapshot_id",
                "payload",
            )
        }

    async with get_db() as primary_db:
        snapshot = (
            await primary_db.execute(
                select(SituationSnapshot).where(SituationSnapshot.snapshot_id == snapshot_id)
            )
        ).scalar_one_or_none()
        if snapshot is None:
            snapshot = SituationSnapshot(
                **historical_values,
                iso_week=(
                    historical_values["period_key"]
                    if historical_values["snapshot_kind"] == "weekly"
                    else None
                ),
                status="published",
            )
            primary_db.add(snapshot)
        newer = (
            await primary_db.execute(
                select(SituationSnapshot).where(
                    SituationSnapshot.snapshot_kind == historical_values["snapshot_kind"],
                    SituationSnapshot.period_key == historical_values["period_key"],
                    SituationSnapshot.revision > historical_values["revision"],
                )
            )
        ).scalars().all()
        for row in newer:
            row.status = "rolled_back"
        snapshot.status = "published"
        primary_db.add(
            SituationOverride(
                target_type="snapshot",
                target_id=snapshot_id,
                action="rollback",
                note=note,
                actor=actor,
                payload={"restored_from": "situation_history_database"},
            )
        )
    audit_id = await record_history_audit(
        target_type="snapshot",
        target_id=snapshot_id,
        action="rollback",
        actor=actor,
        note=note,
        payload={"restored_from": "situation_history_database"},
    )
    return {
        "snapshot_id": snapshot_id,
        "snapshot_kind": historical_values["snapshot_kind"],
        "period_key": historical_values["period_key"],
        "revision": historical_values["revision"],
        "status": "published",
        "audit_id": audit_id,
    }


def serialize_history_snapshot(row: SituationHistorySnapshot, *, include_payload: bool = False) -> dict[str, Any]:
    result = {
        "snapshot_id": row.snapshot_id,
        "snapshot_kind": row.snapshot_kind,
        "period_key": row.period_key,
        "revision": row.revision,
        "supersedes_snapshot_id": row.supersedes_snapshot_id,
        "checked_at": row.checked_at,
        "content_updated_at": row.content_updated_at,
        "data_through": row.data_through,
        "method_version": row.method_version,
        "input_hash": row.input_hash,
        "status": row.operational_status,
        "quality_gate_status": row.quality_gate_status,
        "quality_gate": row.quality_gate,
        "coverage": row.coverage,
        "archived_at": row.archived_at,
        "signal_count": len(row.signals) if "signals" in row.__dict__ else None,
    }
    if include_payload:
        result["payload"] = row.payload
    return result


__all__ = [
    "archive_snapshot",
    "history_health",
    "record_history_audit",
    "restore_history_snapshot",
    "serialize_history_snapshot",
    "sync_history",
]
