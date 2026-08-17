"""Immutable run storage and history-first publication for Situation Room v3."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.config import get_config
from src.core.database import get_db
from src.core.situation_history_database import get_history_db, init_history_database
from src.domain import (
    SituationAnalysisRunV3,
    SituationEventClusterItemV3,
    SituationEventClusterV3,
    SituationPeriodReportV3,
    SituationPublicationPointerV3,
    SituationReportMemberV3,
    SituationReviewDecisionV3,
    SituationSignalResultV3,
)
from src.domain.situation_history import SituationHistoryReportV3, SituationHistorySignalV3

from .contracts import SituationEventClusterV3 as SituationEventClusterContractV3
from .contracts import SituationReportV3, SituationSignalV3


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def report_content_hash(report: SituationReportV3) -> str:
    payload = report.model_dump(mode="json")
    metadata = dict(payload["report"])
    for key in ("report_id", "revision", "supersedes_report_id", "as_of", "status"):
        metadata.pop(key, None)
    payload["report"] = metadata
    for source in payload.get("sources") or []:
        source.pop("checked_at", None)
        source.pop("last_success_at", None)
    return _hash(payload)


async def stage_analysis_run_v3(
    *,
    run_id: str,
    checked_at: datetime,
    method_version: str,
    config_hash: str,
    input_hash: str,
    signals: list[SituationSignalV3],
    ledger: list[dict[str, Any]],
    timings: dict[str, float],
    coverage: dict[str, Any],
) -> SituationAnalysisRunV3:
    signal_by_id = {signal.identity.signal_id: signal for signal in signals}
    async with get_db() as db:
        existing = (
            await db.execute(select(SituationAnalysisRunV3).where(SituationAnalysisRunV3.run_id == run_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        run = SituationAnalysisRunV3(
            run_id=run_id,
            checked_at=checked_at,
            method_version=method_version,
            config_hash=config_hash,
            input_hash=input_hash,
            status="staged",
            timings=timings,
            coverage=coverage,
            quality_gate={},
            ledger_summary={
                "evaluated": len(ledger),
                "modeled": sum(row.get("status") == "modeled" for row in ledger),
                "rejected": sum(row.get("status") == "rejected" for row in ledger),
            },
        )
        db.add(run)
        for index, row in enumerate(ledger):
            signal = signal_by_id.get(str(row.get("signal_id") or ""))
            signal_id = (
                signal.identity.signal_id
                if signal is not None
                else "ledger-v3:" + hashlib.sha256(
                    "|".join(
                        [
                            str(row.get("series_code") or ""),
                            str(row.get("canonical_geography_key") or ""),
                            str(row.get("dimension_key") or ""),
                            str(index),
                        ]
                    ).encode()
                ).hexdigest()[:20]
            )
            payload = signal.model_dump(mode="json") if signal is not None else dict(row)
            db.add(
                SituationSignalResultV3(
                    run_id=run_id,
                    signal_id=signal_id,
                    status=str(row.get("status") or "unknown"),
                    disease_id=row.get("disease_id"),
                    country_code=row.get("country_code"),
                    canonical_geography_key=row.get("canonical_geography_key"),
                    series_code=row.get("series_code"),
                    source_system=row.get("source_system"),
                    metric_type=row.get("metric_type"),
                    cadence=row.get("cadence"),
                    raw_p_value=(signal.anomaly.raw_p_value if signal else row.get("raw_p_value")),
                    q_value=(signal.anomaly.q_value if signal else row.get("q_value")),
                    anomaly_state=(signal.anomaly.state if signal else row.get("anomaly_state")),
                    review_priority=(signal.assessment.review_priority if signal else row.get("review_priority")),
                    rejection_reason=row.get("rejection_reason"),
                    payload=payload,
                )
            )
        await db.flush()
        return run


async def mark_analysis_run_failed_v3(run_id: str, error: BaseException | str) -> None:
    """Close a staged run after publication fails without moving any pointer."""

    message = str(error).strip() or type(error).__name__
    async with get_db() as db:
        run = (
            await db.execute(
                select(SituationAnalysisRunV3).where(SituationAnalysisRunV3.run_id == run_id)
            )
        ).scalar_one_or_none()
        if run is not None and run.status == "staged":
            run.status = "failed"
            run.error = message[:8000]


async def prepare_report_revision_v3(report: SituationReportV3) -> tuple[SituationReportV3, str, bool]:
    content_hash = report_content_hash(report)
    async with get_db() as db:
        latest = (
            await db.execute(
                select(SituationPeriodReportV3)
                .where(
                    SituationPeriodReportV3.report_kind == report.report.kind,
                    SituationPeriodReportV3.period_key == report.report.period_key,
                )
                .order_by(SituationPeriodReportV3.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    archived_latest = None
    if get_config().situation_history_database.enabled:
        await init_history_database()
        async with get_history_db() as history_db:
            archived_latest = (
                await history_db.execute(
                    select(SituationHistoryReportV3)
                    .where(
                        SituationHistoryReportV3.report_kind == report.report.kind,
                        SituationHistoryReportV3.period_key == report.report.period_key,
                    )
                    .order_by(SituationHistoryReportV3.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    if latest is not None and latest.input_hash == content_hash:
        return SituationReportV3.model_validate(latest.payload), content_hash, False
    # A history-only row means archive succeeded but the primary transaction
    # failed. Reuse it when identical so retry can safely finish publication.
    if latest is None and archived_latest is not None and archived_latest.input_hash == content_hash:
        return SituationReportV3.model_validate(archived_latest.payload), content_hash, True
    revision = max(
        latest.revision if latest else 0,
        archived_latest.revision if archived_latest else 0,
    ) + 1
    predecessor = None
    if latest is not None and (archived_latest is None or latest.revision >= archived_latest.revision):
        predecessor = latest.report_id
    elif archived_latest is not None:
        predecessor = archived_latest.report_id
    report.report.revision = revision
    report.report.supersedes_report_id = predecessor
    report.report.report_id = (
        f"situation-v3-{report.report.kind}-{report.report.period_key}-r{revision}"
    )
    return report, content_hash, True


async def archive_report_v3(report: SituationReportV3, input_hash: str) -> None:
    if not get_config().situation_history_database.enabled:
        raise RuntimeError("Situation v3 publication requires the dedicated history database")
    await init_history_database()
    payload = report.model_dump(mode="json")
    async with get_history_db() as db:
        existing = (
            await db.execute(
                select(SituationHistoryReportV3).where(
                    SituationHistoryReportV3.report_id == report.report.report_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.input_hash != input_hash:
                raise RuntimeError("Archived v3 report id has different content")
            return
        archived = SituationHistoryReportV3(
            report_id=report.report.report_id,
            report_kind=report.report.kind,
            period_key=report.report.period_key,
            revision=report.report.revision,
            as_of=report.report.as_of,
            input_hash=input_hash,
            status=report.report.status,
            quality_gate=report.quality_gate.model_dump(mode="json"),
            payload=payload,
        )
        db.add(archived)
        await db.flush()
        for signal in report.signals:
            db.add(
                SituationHistorySignalV3(
                    history_report_id=archived.id,
                    signal_id=signal.identity.signal_id,
                    disease_id=signal.identity.disease_id,
                    country_code=signal.identity.country_code,
                    series_code=signal.identity.series_code,
                    anomaly_state=signal.anomaly.state,
                    q_value=signal.anomaly.q_value,
                    review_priority=signal.assessment.review_priority,
                    payload=signal.model_dump(mode="json"),
                )
            )


async def _persist_event_clusters(db, report: SituationReportV3) -> None:
    for event in report.events:
        row = (
            await db.execute(
                select(SituationEventClusterV3)
                .options(selectinload(SituationEventClusterV3.items))
                .where(SituationEventClusterV3.cluster_id == event.cluster_id)
            )
        ).scalar_one_or_none()
        if row is None:
            existing_updates: set[str] = set()
            row = SituationEventClusterV3(
                cluster_id=event.cluster_id,
                disease_id=event.disease_id,
                disease_name=event.disease_name,
                geographies=event.geographies,
                first_published_at=event.first_published_at.isoformat(),
                last_published_at=event.last_published_at.isoformat(),
                source_state="active",
                review_state="unreviewed",
                corrected_payload={},
            )
            db.add(row)
            await db.flush()
        else:
            # Source refreshes can update source facts, but never operator state.
            row.disease_name = event.disease_name
            row.geographies = event.geographies
            row.first_published_at = min(row.first_published_at, event.first_published_at.isoformat())
            row.last_published_at = max(row.last_published_at, event.last_published_at.isoformat())
            existing_updates = {item.update_id for item in row.items}
        for update in event.updates:
            if update.update_id in existing_updates:
                continue
            db.add(
                SituationEventClusterItemV3(
                    cluster_id=event.cluster_id,
                    update_id=update.update_id,
                    source=update.source,
                    title=update.title,
                    source_url=update.url,
                    published_at=update.published_at.isoformat(),
                    payload=update.model_dump(mode="json"),
                )
            )


async def event_review_states_v3(cluster_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Read operator state separately from acquired event facts."""

    identities = list(dict.fromkeys(cluster_ids))
    if not identities:
        return {}
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationEventClusterV3).where(
                    SituationEventClusterV3.cluster_id.in_(identities)
                )
            )
        ).scalars().all()
    return {
        row.cluster_id: {
            "review_state": row.review_state,
            "corrected_payload": dict(row.corrected_payload or {}),
        }
        for row in rows
    }


async def stable_event_cluster_ids_v3(
    clusters: Iterable[SituationEventClusterContractV3],
) -> dict[str, str]:
    """Resolve provisional event IDs to persisted timelines before review lookup."""

    provisional = list(clusters)
    disease_ids = {cluster.disease_id for cluster in provisional}
    if not disease_ids:
        return {}
    async with get_db() as db:
        existing = (
            await db.execute(
                select(SituationEventClusterV3).where(
                    SituationEventClusterV3.disease_id.in_(disease_ids)
                )
            )
        ).scalars().all()
    output: dict[str, str] = {}
    used: set[str] = set()
    for cluster in provisional:
        geography_codes = {
            str(place.get("code")) for place in cluster.geographies if place.get("code")
        }
        candidates = []
        for row in existing:
            if row.cluster_id in used or row.disease_id != cluster.disease_id:
                continue
            existing_codes = {
                str(place.get("code"))
                for place in (row.geographies or [])
                if isinstance(place, dict) and place.get("code")
            }
            if not geography_codes.intersection(existing_codes):
                continue
            try:
                prior_start = date.fromisoformat(str(row.first_published_at)[:10])
                prior_end = date.fromisoformat(str(row.last_published_at)[:10])
            except ValueError:
                continue
            separated_days = max(
                (cluster.first_published_at - prior_end).days,
                (prior_start - cluster.last_published_at).days,
                0,
            )
            if separated_days <= 45:
                stable_id = (
                    str((row.corrected_payload or {}).get("merged_into_cluster_id"))
                    if row.review_state == "merge"
                    and (row.corrected_payload or {}).get("merged_into_cluster_id")
                    else row.cluster_id
                )
                candidates.append((prior_end, stable_id))
        if candidates:
            stable_id = max(candidates)[1]
            output[cluster.cluster_id] = stable_id
            used.add(stable_id)
    return output


async def signal_review_states_v3(
    signal_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    identities = list(dict.fromkeys(signal_ids))
    if not identities:
        return {}
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationReviewDecisionV3)
                .where(
                    SituationReviewDecisionV3.target_type == "signal",
                    SituationReviewDecisionV3.target_id.in_(identities),
                )
                .order_by(SituationReviewDecisionV3.created_at.desc())
            )
        ).scalars().all()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(
            row.target_id,
            {
                "action": row.action,
                "actor": row.actor,
                "note": row.note,
                "payload": dict(row.payload or {}),
                "created_at": row.created_at,
            },
        )
    return latest


async def _update_staged_analysis_runs_v3(
    db: Any,
    run_ids: Iterable[str],
    *,
    status: str,
    quality_gate: dict[str, Any],
) -> None:
    """Close only the current staged run, never historical period members."""

    for run_id in dict.fromkeys(run_ids):
        if not run_id:
            continue
        run = (
            await db.execute(
                select(SituationAnalysisRunV3).where(
                    SituationAnalysisRunV3.run_id == run_id
                )
            )
        ).scalar_one_or_none()
        if run is not None and run.status == "staged":
            run.status = status
            run.quality_gate = quality_gate


async def publish_report_v3(
    report: SituationReportV3,
    *,
    run_ids: Iterable[str],
    channel: str,
) -> tuple[SituationReportV3, bool]:
    prepared, input_hash, changed = await prepare_report_revision_v3(report)
    if not changed:
        async with get_db() as db:
            await _update_staged_analysis_runs_v3(
                db,
                run_ids,
                status="completed_unchanged",
                quality_gate=prepared.quality_gate.model_dump(mode="json"),
            )
        return prepared, False
    if not prepared.quality_gate.passed:
        async with get_db() as db:
            await _update_staged_analysis_runs_v3(
                db,
                run_ids,
                status="gate_failed",
                quality_gate=prepared.quality_gate.model_dump(mode="json"),
            )
        return prepared, False
    # The durable archive must commit before the public pointer can advance.
    await archive_report_v3(prepared, input_hash)
    payload = prepared.model_dump(mode="json")
    async with get_db() as db:
        stored = SituationPeriodReportV3(
            report_id=prepared.report.report_id,
            report_kind=prepared.report.kind,
            period_key=prepared.report.period_key,
            period_start=prepared.report.period_start.isoformat(),
            period_end=prepared.report.period_end.isoformat(),
            as_of=prepared.report.as_of,
            revision=prepared.report.revision,
            supersedes_report_id=prepared.report.supersedes_report_id,
            method_version=prepared.method.version,
            config_hash=prepared.method.config_hash,
            input_hash=input_hash,
            status="published",
            quality_gate=prepared.quality_gate.model_dump(mode="json"),
            coverage=prepared.coverage.model_dump(mode="json"),
            payload=payload,
        )
        db.add(stored)
        await db.flush()
        member_ids = list(dict.fromkeys(run_ids))
        for run_id in member_ids:
            db.add(SituationReportMemberV3(report_id=stored.report_id, run_id=run_id))
        await _update_staged_analysis_runs_v3(
            db,
            member_ids,
            status="published",
            quality_gate=prepared.quality_gate.model_dump(mode="json"),
        )
        await _persist_event_clusters(db, prepared)
        pointer = (
            await db.execute(
                select(SituationPublicationPointerV3).where(
                    SituationPublicationPointerV3.channel == channel
                )
            )
        ).scalar_one_or_none()
        if pointer is None:
            pointer = SituationPublicationPointerV3(
                channel=channel,
                report_id=stored.report_id,
                published_at=utc_now(),
                previous_report_id=None,
            )
            db.add(pointer)
        else:
            pointer.previous_report_id = pointer.report_id
            pointer.report_id = stored.report_id
            pointer.published_at = utc_now()
    return prepared, True


async def latest_report_v3(channel: str = "latest") -> dict[str, Any] | None:
    async with get_db() as db:
        pointer = (
            await db.execute(
                select(SituationPublicationPointerV3).where(
                    SituationPublicationPointerV3.channel == channel
                )
            )
        ).scalar_one_or_none()
        if pointer is None:
            return None
        report = (
            await db.execute(
                select(SituationPeriodReportV3).where(
                    SituationPeriodReportV3.report_id == pointer.report_id,
                    SituationPeriodReportV3.status == "published",
                )
            )
        ).scalar_one_or_none()
    return dict(report.payload) if report else None


async def reports_v3(kind: str | None = None) -> list[dict[str, Any]]:
    query = select(SituationPeriodReportV3).where(SituationPeriodReportV3.status == "published")
    if kind:
        query = query.where(SituationPeriodReportV3.report_kind == kind)
    query = query.order_by(
        SituationPeriodReportV3.period_key.desc(), SituationPeriodReportV3.revision.desc()
    )
    async with get_db() as db:
        rows = (await db.execute(query)).scalars().all()
    latest_by_period: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        latest_by_period.setdefault((row.report_kind, row.period_key), dict(row.payload))
    return list(latest_by_period.values())


async def daily_reports_for_period_v3(start: date, end: date) -> list[tuple[str, SituationReportV3]]:
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationPeriodReportV3)
                .options(selectinload(SituationPeriodReportV3.members))
                .where(
                    SituationPeriodReportV3.report_kind == "daily",
                    SituationPeriodReportV3.period_start >= start.isoformat(),
                    SituationPeriodReportV3.period_start <= end.isoformat(),
                    SituationPeriodReportV3.status == "published",
                )
                .order_by(SituationPeriodReportV3.period_start, SituationPeriodReportV3.revision.desc())
            )
        ).scalars().unique().all()
    latest_by_day: dict[str, SituationPeriodReportV3] = {}
    for row in rows:
        latest_by_day.setdefault(row.period_key, row)
    return [
        (
            row.members[0].run_id if row.members else "",
            SituationReportV3.model_validate(row.payload),
        )
        for row in latest_by_day.values()
    ]


async def active_signal_ids_before_period_v3(kind: str, start: date) -> set[str]:
    """Return the prior period's active-at-end identities for lifecycle labels."""

    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationPeriodReportV3)
                .where(
                    SituationPeriodReportV3.report_kind == kind,
                    SituationPeriodReportV3.period_end < start.isoformat(),
                    SituationPeriodReportV3.status == "published",
                )
                .order_by(
                    SituationPeriodReportV3.period_end.desc(),
                    SituationPeriodReportV3.revision.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return set()
    report = SituationReportV3.model_validate(row.payload)
    return {
        signal.identity.signal_id
        for signal in report.signals
        if signal.lifecycle.status != "resolved"
    }


__all__ = [
    "archive_report_v3",
    "active_signal_ids_before_period_v3",
    "daily_reports_for_period_v3",
    "event_review_states_v3",
    "signal_review_states_v3",
    "stable_event_cluster_ids_v3",
    "latest_report_v3",
    "mark_analysis_run_failed_v3",
    "prepare_report_revision_v3",
    "publish_report_v3",
    "report_content_hash",
    "reports_v3",
    "stage_analysis_run_v3",
]
