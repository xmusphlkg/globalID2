"""Thin, typed operational API for Situation Room v3."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.domain import (
    SituationAnalysisRunV3,
    SituationCalibrationRunV3,
    SituationEventClusterV3,
    SituationEventLabelV3,
    SituationPeriodReportV3,
    SituationPublicationPointerV3,
    SituationPolicyDecisionV3,
    SituationReviewDecisionV3,
    SituationSignalResultV3,
)
from src.services.situation_v3.contracts import SituationReportV3


router = APIRouter(prefix="/situation/v3")


class ReviewDecisionRequest(BaseModel):
    action: Literal[
        "publish",
        "verify",
        "reject",
        "suppress",
        "correct",
        "merge",
        "rollback",
    ]
    note: str = Field(min_length=3, max_length=4000)
    actor: str | None = Field(default="dashboard", min_length=3, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("note")
    @classmethod
    def note_must_be_meaningful(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("note must contain at least three non-space characters")
        return cleaned

    @field_validator("actor")
    @classmethod
    def actor_must_be_meaningful(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("actor must contain at least three non-space characters")
        return cleaned


class RollbackRequest(BaseModel):
    report_id: str = Field(min_length=3, max_length=140)
    note: str = Field(min_length=3, max_length=4000)
    actor: str | None = Field(default="dashboard", max_length=160)


def _is_http_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _decision_id(target_type: str, target_id: str, action: str, note: str) -> str:
    seed = f"{target_type}|{target_id}|{action}|{note}|{datetime.now(timezone.utc).isoformat()}"
    return "decision-v3:" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _page_headers(response: Response, total: int, page: int, page_size: int) -> None:
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str((page - 1) * page_size)


def _calibration_payload(row: SituationCalibrationRunV3) -> dict[str, Any]:
    return {
        "calibration_id": row.calibration_id,
        "method_version": row.method_version,
        "config_hash": row.config_hash,
        "artifact_hash": row.artifact_hash,
        "artifact_uri": row.artifact_uri,
        "status": row.status,
        "calibrated_at": row.calibrated_at,
        "window_start": row.window_start,
        "window_end": row.window_end,
        "summary": dict(row.summary or {}),
        "group_results": dict(row.group_results or {}),
    }


@router.get("/calibration/latest")
async def latest_calibration_v3() -> dict[str, Any]:
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationCalibrationRunV3)
                .order_by(SituationCalibrationRunV3.calibrated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return {
            "status": "not_available",
            "automation_supported": False,
            "reason": "No registered Situation v3.2 calibration artifact",
        }
    payload = _calibration_payload(row)
    payload["automation_supported"] = row.status == "supported"
    return payload


@router.get("/calibration")
async def calibration_runs_v3(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = []
    if status:
        filters.append(SituationCalibrationRunV3.status == status)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(SituationCalibrationRunV3)
                    .where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationCalibrationRunV3)
                .where(*filters)
                .order_by(SituationCalibrationRunV3.calibrated_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [_calibration_payload(row) for row in rows]


@router.get("/event-labels")
async def event_labels_v3(
    response: Response,
    disease_id: str | None = None,
    adjudication: str | None = None,
    split: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = []
    if disease_id:
        filters.append(SituationEventLabelV3.disease_id == disease_id)
    if adjudication:
        filters.append(SituationEventLabelV3.adjudication == adjudication)
    if split:
        filters.append(SituationEventLabelV3.split == split)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(SituationEventLabelV3)
                    .where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationEventLabelV3)
                .where(*filters)
                .order_by(
                    SituationEventLabelV3.first_official_published_at.desc(),
                    SituationEventLabelV3.label_id,
                )
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [row.to_dict() for row in rows]


@router.get("/policy-decisions")
async def policy_decisions_v3(
    response: Response,
    run_id: str | None = None,
    signal_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = []
    if run_id:
        filters.append(SituationPolicyDecisionV3.run_id == run_id)
    if signal_id:
        filters.append(SituationPolicyDecisionV3.signal_id == signal_id)
    if status:
        filters.append(SituationPolicyDecisionV3.status == status)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(SituationPolicyDecisionV3)
                    .where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationPolicyDecisionV3)
                .where(*filters)
                .order_by(SituationPolicyDecisionV3.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [row.to_dict() for row in rows]


@router.get("/overview")
async def overview_v3() -> dict[str, Any]:
    async with get_db() as db:
        pointer = (
            await db.execute(
                select(SituationPublicationPointerV3).where(
                    SituationPublicationPointerV3.channel == "latest"
                )
            )
        ).scalar_one_or_none()
        report = None
        if pointer:
            report = (
                await db.execute(
                    select(SituationPeriodReportV3).where(
                        SituationPeriodReportV3.report_id == pointer.report_id
                    )
                )
            ).scalar_one_or_none()
        latest_run = (
            await db.execute(
                select(SituationAnalysisRunV3)
                .order_by(SituationAnalysisRunV3.checked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        failure_rows = (
            await db.execute(
                select(
                    SituationSignalResultV3.rejection_reason,
                    func.count(SituationSignalResultV3.id),
                )
                .where(
                    SituationSignalResultV3.run_id == (latest_run.run_id if latest_run else ""),
                    SituationSignalResultV3.rejection_reason.is_not(None),
                )
                .group_by(SituationSignalResultV3.rejection_reason)
            )
        ).all()
    payload = SituationReportV3.model_validate(report.payload) if report else None
    return {
        "schema_version": "situation_room.v3",
        "publication": {
            "channel": pointer.channel if pointer else "latest",
            "report_id": pointer.report_id if pointer else None,
            "previous_report_id": pointer.previous_report_id if pointer else None,
            "published_at": pointer.published_at if pointer else None,
        },
        "report": payload,
        "latest_run": (
            {
                "run_id": latest_run.run_id,
                "checked_at": latest_run.checked_at,
                "status": latest_run.status,
                "method_version": latest_run.method_version,
                "timings": latest_run.timings,
                "coverage": latest_run.coverage,
                "quality_gate": latest_run.quality_gate,
                "ledger_summary": latest_run.ledger_summary,
                "model_failures": {str(reason): int(count) for reason, count in failure_rows},
            }
            if latest_run
            else None
        ),
    }


@router.get("/runs")
async def runs_v3(
    response: Response,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
) -> list[dict[str, Any]]:
    query = select(SituationAnalysisRunV3)
    count_query = select(func.count()).select_from(SituationAnalysisRunV3)
    if status:
        query = query.where(SituationAnalysisRunV3.status == status)
        count_query = count_query.where(SituationAnalysisRunV3.status == status)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int((await db.execute(count_query)).scalar_one() or 0)
        rows = (
            await db.execute(
                query.order_by(SituationAnalysisRunV3.checked_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [row.to_dict() for row in rows]


@router.get("/signals")
async def signals_v3(
    response: Response,
    run_id: str | None = None,
    state: str | None = None,
    source_system: str | None = None,
    rejection_reason: str | None = None,
    query_text: str | None = Query(default=None, alias="q"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    async with get_db() as db:
        if not run_id:
            run_id = (
                await db.execute(
                    select(SituationAnalysisRunV3.run_id)
                    .order_by(SituationAnalysisRunV3.checked_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not run_id:
            _page_headers(response, 0, page, page_size)
            return []
        filters = [SituationSignalResultV3.run_id == run_id]
        if state:
            filters.append(SituationSignalResultV3.anomaly_state == state)
        if source_system:
            filters.append(SituationSignalResultV3.source_system == source_system)
        if rejection_reason:
            filters.append(SituationSignalResultV3.rejection_reason == rejection_reason)
        if query_text:
            needle = f"%{query_text.strip()}%"
            filters.append(
                SituationSignalResultV3.disease_id.ilike(needle)
                | SituationSignalResultV3.series_code.ilike(needle)
                | SituationSignalResultV3.canonical_geography_key.ilike(needle)
            )
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(SituationSignalResultV3).where(*filters)
                )
            ).scalar_one()
            or 0
        )
        offset = (page - 1) * page_size
        rows = (
            await db.execute(
                select(SituationSignalResultV3)
                .where(*filters)
                .order_by(
                    SituationSignalResultV3.q_value.asc().nulls_last(),
                    SituationSignalResultV3.id,
                )
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [row.to_dict() for row in rows]


@router.get("/reports")
async def reports_v3(
    response: Response,
    kind: Literal["daily", "weekly", "monthly"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = [SituationPeriodReportV3.status == "published"]
    if kind:
        filters.append(SituationPeriodReportV3.report_kind == kind)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(SituationPeriodReportV3).where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationPeriodReportV3)
                .options(selectinload(SituationPeriodReportV3.members))
                .where(*filters)
                .order_by(
                    SituationPeriodReportV3.period_key.desc(),
                    SituationPeriodReportV3.revision.desc(),
                )
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().unique().all()
    _page_headers(response, total, page, page_size)
    return [
        {
            **{key: value for key, value in row.to_dict().items() if key != "payload"},
            "summary": (row.payload or {}).get("summary") or {},
            "sources": (row.payload or {}).get("sources") or [],
            "run_ids": [member.run_id for member in row.members],
        }
        for row in rows
    ]


@router.get("/reports/compare")
async def compare_reports_v3(from_report: str, to_report: str) -> dict[str, Any]:
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationPeriodReportV3).where(
                    SituationPeriodReportV3.report_id.in_([from_report, to_report])
                )
            )
        ).scalars().all()
    by_id = {row.report_id: SituationReportV3.model_validate(row.payload) for row in rows}
    if from_report not in by_id or to_report not in by_id:
        raise HTTPException(404, "One or both Situation v3 reports were not found")
    before, after = by_id[from_report], by_id[to_report]
    before_signals = {row.identity.signal_id: row for row in before.signals}
    after_signals = {row.identity.signal_id: row for row in after.signals}
    shared = sorted(before_signals.keys() & after_signals.keys())
    changed = [
        signal_id
        for signal_id in shared
        if before_signals[signal_id].model_dump(mode="json")
        != after_signals[signal_id].model_dump(mode="json")
    ]
    return {
        "from_report": from_report,
        "to_report": to_report,
        "signals": {
            "added": sorted(after_signals.keys() - before_signals.keys()),
            "removed": sorted(before_signals.keys() - after_signals.keys()),
            "changed": changed,
        },
        "coverage": {"before": before.coverage, "after": after.coverage},
        "sources": {"before": before.sources, "after": after.sources},
        "method": {"before": before.method, "after": after.method},
        "quality_gate": {"before": before.quality_gate, "after": after.quality_gate},
    }


@router.get("/reports/{report_id}", response_model=SituationReportV3)
async def report_v3(report_id: str) -> SituationReportV3:
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationPeriodReportV3).where(
                    SituationPeriodReportV3.report_id == report_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Situation v3 report not found")
    return SituationReportV3.model_validate(row.payload)


@router.get("/events")
async def events_v3(
    response: Response,
    review_state: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = []
    if review_state:
        filters.append(SituationEventClusterV3.review_state == review_state)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(SituationEventClusterV3).where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationEventClusterV3)
                .options(selectinload(SituationEventClusterV3.items))
                .where(*filters)
                .order_by(SituationEventClusterV3.last_published_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().unique().all()
    _page_headers(response, total, page, page_size)
    return [
        {
            **row.to_dict(),
            "updates": [item.to_dict() for item in row.items],
        }
        for row in rows
    ]


@router.get("/audit")
async def audit_v3(
    response: Response,
    target_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
) -> list[dict[str, Any]]:
    filters = []
    if target_type:
        filters.append(SituationReviewDecisionV3.target_type == target_type)
    offset = (page - 1) * page_size
    async with get_db() as db:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(SituationReviewDecisionV3).where(*filters)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await db.execute(
                select(SituationReviewDecisionV3)
                .where(*filters)
                .order_by(SituationReviewDecisionV3.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
    _page_headers(response, total, page, page_size)
    return [row.to_dict() for row in rows]


@router.post("/review/{target_type}/{target_id}")
async def review_v3(
    target_type: Literal["event", "signal", "report"],
    target_id: str,
    body: ReviewDecisionRequest,
) -> dict[str, Any]:
    if body.action == "rollback":
        raise HTTPException(422, "Use the publication rollback endpoint")
    allowed_actions = {
        "event": {"publish", "suppress", "correct", "merge"},
        "signal": {"verify", "reject", "suppress"},
        "report": set(),
    }
    if body.action not in allowed_actions[target_type]:
        if target_type == "report":
            raise HTTPException(422, "Use the publication rollback endpoint for reports")
        raise HTTPException(
            422,
            f"Action {body.action!r} is not valid for {target_type} reviews",
        )
    if target_type == "event" and body.action == "correct":
        corrected_name = body.payload.get("disease_name")
        corrected_geographies = body.payload.get("geographies")
        valid_name = isinstance(corrected_name, str) and bool(corrected_name.strip())
        valid_geographies = (
            isinstance(corrected_geographies, list)
            and bool(corrected_geographies)
            and all(
                isinstance(place, dict)
                and isinstance(place.get("code"), str)
                and bool(place["code"].strip())
                for place in corrected_geographies
            )
        )
        if not valid_name and not valid_geographies:
            raise HTTPException(
                422,
                "Event corrections require a non-empty disease_name or valid geographies",
            )
        if valid_name:
            body.payload["disease_name"] = corrected_name.strip()[:300]
        if corrected_geographies is not None:
            if not valid_geographies:
                raise HTTPException(422, "Corrected geographies require non-empty codes")
            body.payload["geographies"] = [
                {
                    "code": str(place["code"]).strip(),
                    "name": str(place.get("name") or place["code"]).strip(),
                }
                for place in corrected_geographies
            ]
    if target_type == "event" and body.action == "merge" and not body.payload.get(
        "merged_into_cluster_id"
    ):
        raise HTTPException(422, "Event merges require merged_into_cluster_id")
    if target_type == "signal" and body.payload.get("risk_level"):
        if body.payload.get("risk_level") not in {"low", "moderate", "high", "very_high"}:
            raise HTTPException(422, "risk_level is invalid")
        if body.action != "verify" or not body.payload.get("risk_rationale"):
            raise HTTPException(
                422,
                "Audited risk requires signal verification and risk_rationale",
            )
        evidence_url = body.payload.get("evidence_url")
        if evidence_url and not _is_http_url(evidence_url):
            raise HTTPException(422, "evidence_url must be an absolute HTTP(S) URL")
    decision_id = _decision_id(target_type, target_id, body.action, body.note)
    async with get_db() as db:
        if target_type == "event":
            event = (
                await db.execute(
                    select(SituationEventClusterV3)
                    .options(selectinload(SituationEventClusterV3.items))
                    .where(SituationEventClusterV3.cluster_id == target_id)
                )
            ).scalar_one_or_none()
            if event is None:
                raise HTTPException(404, "Situation v3 event cluster not found")
            if body.action == "merge":
                merged_into = str(body.payload["merged_into_cluster_id"])
                if merged_into == target_id:
                    raise HTTPException(422, "An event cluster cannot merge into itself")
                merge_target = (
                    await db.execute(
                        select(SituationEventClusterV3)
                        .options(selectinload(SituationEventClusterV3.items))
                        .where(SituationEventClusterV3.cluster_id == merged_into)
                    )
                ).scalar_one_or_none()
                if merge_target is None:
                    raise HTTPException(404, "Merge target event cluster not found")
                if merge_target.disease_id != event.disease_id:
                    raise HTTPException(
                        422,
                        "Only event clusters for the same disease can be merged",
                    )
                if merge_target.review_state in {
                    "merge",
                    "merged",
                    "suppress",
                    "suppressed",
                }:
                    raise HTTPException(422, "Merge target must be an active event cluster")
                geographies = {
                    str(place.get("code")): dict(place)
                    for place in [
                        *(merge_target.geographies or []),
                        *(event.geographies or []),
                    ]
                    if isinstance(place, dict) and place.get("code")
                }
                merge_target.geographies = [
                    geographies[code] for code in sorted(geographies)
                ]
                merge_target.first_published_at = min(
                    merge_target.first_published_at,
                    event.first_published_at,
                )
                merge_target.last_published_at = max(
                    merge_target.last_published_at,
                    event.last_published_at,
                )
                target_update_ids = {item.update_id for item in merge_target.items}
                for item in list(event.items):
                    if item.update_id not in target_update_ids:
                        item.cluster = merge_target
            event.review_state = body.action
            if body.action in {"correct", "merge"}:
                event.corrected_payload = body.payload
        elif target_type == "signal":
            signal_exists = (
                await db.execute(
                    select(SituationSignalResultV3.id)
                    .where(SituationSignalResultV3.signal_id == target_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if signal_exists is None:
                raise HTTPException(404, "Situation v3 signal not found")
        db.add(
            SituationReviewDecisionV3(
                decision_id=decision_id,
                target_type=target_type,
                target_id=target_id,
                action=body.action,
                actor=body.actor,
                note=body.note,
                payload=body.payload,
            )
        )
    return {"decision_id": decision_id, "target_type": target_type, "target_id": target_id}


@router.post("/publication/{channel}/rollback")
async def rollback_v3(channel: str, body: RollbackRequest) -> dict[str, Any]:
    decision_id = _decision_id("publication", channel, "rollback", body.note)
    async with get_db() as db:
        report = (
            await db.execute(
                select(SituationPeriodReportV3).where(
                    SituationPeriodReportV3.report_id == body.report_id,
                    SituationPeriodReportV3.status == "published",
                )
            )
        ).scalar_one_or_none()
        if report is None or not bool((report.quality_gate or {}).get("passed")):
            raise HTTPException(422, "Rollback target must be a gate-passed published report")
        pointer = (
            await db.execute(
                select(SituationPublicationPointerV3).where(
                    SituationPublicationPointerV3.channel == channel
                )
            )
        ).scalar_one_or_none()
        if pointer is None:
            raise HTTPException(404, "Situation v3 publication channel not found")
        previous = pointer.report_id
        pointer.previous_report_id = previous
        pointer.report_id = report.report_id
        pointer.published_at = datetime.now(timezone.utc).replace(microsecond=0)
        db.add(
            SituationReviewDecisionV3(
                decision_id=decision_id,
                target_type="publication",
                target_id=channel,
                action="rollback",
                actor=body.actor,
                note=body.note,
                payload={"from_report_id": previous, "to_report_id": report.report_id},
            )
        )
    return {
        "decision_id": decision_id,
        "channel": channel,
        "previous_report_id": previous,
        "report_id": report.report_id,
    }


__all__ = ["router"]
