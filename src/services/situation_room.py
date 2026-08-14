"""Deterministic orchestration for Global Infectious Disease Situation Room v2."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, select, text

from src.core.database import get_db, init_database
from src.domain import SituationSnapshot
from src.services.situation_events import (
    EVENT_SOURCE_URLS,
    fetch_cdc_respiratory_history,
    fetch_external_events,
    persist_cdc_respiratory_series,
    persist_events,
    published_events,
)
from src.services.situation_quality import evaluate_quality_gate
from src.services.situation_history_service import archive_snapshot
from src.services.situation_statistics import (
    RISK_WEIGHTS,
    analyze_frame,
    analyze_series,
    compute_risk,
    evaluate_frame,
    evaluate_frame_with_ledger,
    summarize_analysis_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "situation_room.json"
METHOD_VERSION = "situation_room_v2.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("method_version", METHOD_VERSION)
    config.setdefault("thresholds", {})
    config.setdefault("metric_policy", {})
    return config


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


async def fetch_series_frame(config: dict[str, Any] | None = None) -> pd.DataFrame:
    """Read exact source-native series using the current registry contract.

    SQL errors intentionally propagate: returning an empty frame would turn a
    broken data contract into an apparently healthy empty public snapshot.
    """
    config = config or load_config()
    metric_policy = config.get("metric_policy", {})
    activity_metrics = list(metric_policy.get("activity_metrics") or ["case_notifications"])
    severity_metrics = list(metric_policy.get("severity_metrics") or ["hospitalized_case_notifications"])
    allowed_metrics = sorted(set(activity_metrics + severity_metrics))
    if not allowed_metrics:
        return pd.DataFrame()
    metric_literals = ", ".join("'" + metric.replace("'", "''") + "'" for metric in allowed_metrics)
    query = text(
        f"""
        SELECT o.time, o.value, o.quality_status, o.geography_key,
               o.dimension_key, o.dimensions, o.series_code,
               s.disease_id, s.country_code, s.source_system, s.source_label,
               s.metric_type, s.reporting_basis, s.temporal_granularity,
               s.unit, s.aggregation_policy, s.missing_value_policy,
               s.metadata AS series_metadata,
               d.standard_name_en AS disease_name,
               c.name_en AS country_name
          FROM disease_series_observations o
          JOIN disease_surveillance_series s ON s.series_code = o.series_code
          LEFT JOIN standard_diseases d ON d.disease_id = s.disease_id
          LEFT JOIN countries c ON c.code = s.country_code
         WHERE o.suppressed = false
           AND o.quality_status <> 'rejected'
           AND s.is_active = true
           AND s.disease_id IS NOT NULL
           AND s.metric_type IN ({metric_literals})
           AND s.mapping_relation = 'exact'
           AND s.aggregation_policy IN ('non_additive', 'direct_only', 'reported_aggregate')
           AND s.temporal_granularity IN ('daily', 'weekly', 'monthly')
           AND s.missing_value_policy <> 'missing_is_zero'
        """
    )
    async with get_db() as db:
        rows = (await db.execute(query)).mappings().all()
    frame = pd.DataFrame([dict(row) for row in rows])
    if not frame.empty:
        frame["disease_slug"] = frame["disease_name"].fillna(frame["disease_id"]).map(_slug)
        frame["source_url"] = frame["series_metadata"].map(
            lambda metadata: (metadata or {}).get("source_url") or (metadata or {}).get("source_uri") if isinstance(metadata, dict) else None
        )
    return frame


def _official_dimension(event: dict[str, Any]) -> float | None:
    if event.get("official_concern_score") is not None:
        return float(event["official_concern_score"])
    # Publication in a contracted official outbreak/event feed is itself an
    # explicit event classification, but not an agency risk rating.
    return 40.0 if event.get("kind") == "official_event" else None


def apply_composite_risk(
    assessments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    activity_metrics = set(config.get("metric_policy", {}).get("activity_metrics") or [])
    severity_metrics = set(config.get("metric_policy", {}).get("severity_metrics") or [])
    severity_by_scope: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in assessments:
        if row.get("metric_type") in severity_metrics:
            key = (row.get("disease_id"), row.get("country_code"), row.get("geography_key"), row.get("cadence"))
            severity_by_scope[key] = row
    output: list[dict[str, Any]] = []
    for original in assessments:
        if activity_metrics and original.get("metric_type") not in activity_metrics:
            continue
        row = dict(original)
        key = (row.get("disease_id"), row.get("country_code"), row.get("geography_key"), row.get("cadence"))
        severity = severity_by_scope.get(key)
        matching_events = [
            event
            for event in events
            if event.get("disease_id") == row.get("disease_id")
            and any(place.get("code") == row.get("country_code") for place in event.get("geographies") or [])
        ]
        official_scores = [score for score in (_official_dimension(event) for event in matching_events) if score is not None]
        official = max(official_scores) if official_scores else None
        # Geographic spread is only asserted when the official evidence names
        # more than one distinct jurisdiction.  A single place is not treated
        # as proof of spread.
        event_geographies = {
            place.get("code")
            for event in matching_events
            for place in event.get("geographies") or []
            if place.get("code")
        }
        geographic = min(100.0, 35.0 + 15.0 * len(event_geographies)) if len(event_geographies) >= 2 else None
        trend = (row.get("risk") or {}).get("dimensions", {}).get("trend")
        severity_score = (severity.get("risk") or {}).get("dimensions", {}).get("trend") if severity else None
        row["risk"] = compute_risk(
            {
                "trend": trend,
                "severity": severity_score,
                "geographic_spread": geographic,
                "official_concern": official,
            },
            config.get("risk", {}).get("weights") or RISK_WEIGHTS,
        )
        row["confidence"] = row["risk"]["confidence"]
        if matching_events:
            row["evidence_links"] = [*(row.get("evidence_links") or []), *[link for event in matching_events for link in event.get("evidence_links") or []]]
        output.append(row)
    output.sort(
        key=lambda item: (
            bool(item.get("candidate")),
            item.get("risk", {}).get("score", 0),
            item.get("statistics", {}).get("detector_votes", 0),
        ),
        reverse=True,
    )
    return output


def attach_event_usage(
    events: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Explain exactly how each official event affected the numerical model."""
    output: list[dict[str, Any]] = []
    for original in events:
        event = dict(original)
        geography_codes = {
            place.get("code")
            for place in event.get("geographies") or []
            if place.get("code")
        }
        matched = [
            row
            for row in assessments
            if row.get("disease_id") == event.get("disease_id")
            and row.get("country_code") in geography_codes
        ]
        concern_applied = [
            row.get("id")
            for row in matched
            if ((row.get("risk") or {}).get("dimensions") or {}).get("official_concern")
            is not None
        ]
        event["usage"] = {
            "emerging_evidence": True,
            "matched_numeric_series_count": len(matched),
            "matched_signal_ids": [row.get("id") for row in matched if row.get("id")],
            "official_concern_applied_count": len(concern_applied),
            "official_concern_applied_signal_ids": concern_applied,
            "status": "used_in_composite_risk" if concern_applied else "event_evidence_only",
            "not_used_in_risk_reason": (
                None
                if concern_applied
                else "No eligible exact disease-and-jurisdiction numerical series in this snapshot"
            ),
        }
        output.append(event)
    return output


def _respiratory_placeholders() -> list[dict[str, Any]]:
    return [
        {"disease_id": "D038", "disease_name": "Influenza", "disease_slug": "influenza"},
        {"disease_id": "D142", "disease_name": "Respiratory syncytial virus infection (RSV)", "disease_slug": "respiratory-syncytial-virus-infection-rsv"},
        {"disease_id": "D004", "disease_name": "COVID-19", "disease_slug": "covid-19"},
    ]


def select_respiratory(assessments: list[dict[str, Any]], external_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for identity in _respiratory_placeholders():
        cards = [row for row in external_cards if row.get("disease_id") == identity["disease_id"]]
        analyzed = [row for row in assessments if row.get("disease_id") == identity["disease_id"]]
        matches = [*cards, *analyzed]
        # Prefer a fully analyzed series when data-through dates tie. The raw
        # CDC card is then attached as metric context instead of hiding the
        # detector output that was computed from the same source history.
        matches.sort(
            key=lambda row: (
                str(row.get("data_through") or ""),
                bool(row.get("statistics")),
            ),
            reverse=True,
        )
        if matches:
            choice = dict(matches[0])
            if choice.get("statistics") and cards:
                latest_card = max(cards, key=lambda row: str(row.get("data_through") or ""))
                choice["metrics"] = latest_card.get("metrics") or []
                choice["context_source"] = {
                    "source_system": latest_card.get("source_system"),
                    "source_label": latest_card.get("source_label"),
                    "data_through": latest_card.get("data_through"),
                }
            selected.append(choice)
        else:
            selected.append(
                {
                    "id": "respiratory-unavailable:" + identity["disease_slug"],
                    "kind": "respiratory_status",
                    **identity,
                    "data_through": None,
                    "window": {"label": "No current eligible series"},
                    "quality": {"status": "unavailable"},
                    "risk": {"score": None, "level": "not_assessed", "confidence": "low", "missing_dimensions": ["current_data"]},
                    "evidence_links": [],
                }
            )
    return selected


def _deterministic_narrative(period_key: str, coverage: dict[str, Any]) -> dict[str, str]:
    try:
        month = datetime.strptime(period_key[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        month = period_key
    series = int(coverage.get("analyzed_series_count") or 0)
    jurisdictions = int(coverage.get("jurisdiction_count") or coverage.get("country_count") or 0)
    return {
        "en": f"{month} analyzed {series} source-native series across {jurisdictions} GIDS-covered jurisdictions. Signals are statistical screening results, not outbreak declarations.",
        "zh": f"{period_key[:7]} 共分析 {series} 条来源原生序列，覆盖 GIDS 当前支持的 {jurisdictions} 个辖区。信号属于统计筛查结果，并非暴发宣布。",
    }


def build_snapshot(
    signals: list[dict[str, Any]],
    events: list[dict[str, Any]],
    freshness: dict[str, Any],
    config: dict[str, Any],
    generated_at: datetime | None = None,
    *,
    assessments: list[dict[str, Any]] | None = None,
    respiratory_cards: list[dict[str, Any]] | None = None,
    rejected_reasons: dict[str, int] | None = None,
    source_series_count: int | None = None,
    analysis_summary: dict[str, Any] | None = None,
    analysis_ledger: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    checked_at = generated_at or utc_now()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    all_assessments = assessments if assessments is not None else signals
    increasing = [row for row in signals if row.get("candidate", True)]
    unusual = [row for row in increasing if row.get("unusual")]
    respiratory = select_respiratory(all_assessments, respiratory_cards or [])
    data_dates = [
        str(row["data_through"])
        for row in [*all_assessments, *respiratory]
        if row.get("data_through")
    ]
    iso = checked_at.isocalendar()
    iso_week = f"{iso.year}-W{iso.week:02d}"
    limits = config.get("signal_limits") or {}
    executed_series_count = int((analysis_summary or {}).get("series_count") or 0)
    analyzed_series_count = int((analysis_summary or {}).get("analyzed_count") or len(all_assessments))
    coverage = {
        "source_series_count": executed_series_count or (source_series_count if source_series_count is not None else len(all_assessments)),
        "registered_series_count": source_series_count if source_series_count is not None else len(all_assessments),
        "analyzed_series_count": analyzed_series_count,
        "candidate_signal_count": len(increasing),
        "country_count": len({row.get("country_code") for row in all_assessments if row.get("country_code")}),
        "jurisdiction_count": len({(row.get("country_code"), row.get("geography_key")) for row in all_assessments if row.get("country_code")}),
        "disease_count": len({row.get("disease_id") for row in all_assessments if row.get("disease_id")}),
        "rejected_reasons": rejected_reasons or {},
        "note_en": "Statistical signals cover GIDS-supported jurisdictions only; Global does not mean exhaustive worldwide surveillance.",
        "note_zh": "统计信号仅覆盖 GIDS 当前支持的辖区；Global 不代表穷尽全球监测。",
    }
    payload: dict[str, Any] = {
        "schema_version": "situation_room.v2",
        "method_version": config.get("method_version", METHOD_VERSION),
        "snapshot_id": "situation-" + checked_at.strftime("%Y%m%dT%H%M%SZ"),
        "snapshot_kind": "daily",
        "period_key": checked_at.date().isoformat(),
        "revision": 1,
        "supersedes_snapshot_id": None,
        "public_enabled": bool(config.get("public_enabled", False)),
        "generated_at": checked_at.isoformat(),
        "checked_at": checked_at.isoformat(),
        "content_updated_at": checked_at.isoformat(),
        "data_through": max(data_dates) if data_dates else None,
        "iso_week": iso_week,
        "coverage": coverage,
        "analysis_execution": analysis_summary or {},
        "freshness": freshness,
        "increasing": increasing[: int(limits.get("increasing", 12))],
        "respiratory": respiratory[: int(limits.get("respiratory", 9))],
        "emerging": events[: int(limits.get("emerging", 9))],
        "unusual": unusual[: int(limits.get("unusual", 9))],
        "event_sources": [{"id": key, "url": value} for key, value in EVENT_SOURCE_URLS.items()],
        "methodology": {
            "en": "Each exact source-native series is screened with a same-season baseline, standard and robust z-scores, season-adjusted EWMA, Bayesian online change-point detection, and detector voting. Composite risk reweights only available epidemiological dimensions.",
            "zh": "每条精确映射的来源原生序列均使用同季节基线、标准及稳健 z-score、季节调整 EWMA、贝叶斯在线变点检测和检测器投票；综合风险只在已有流行病学维度内重分配权重。",
        },
        "limitations": {
            "en": "Official event feeds are attributable but not exhaustive. Reporting lag, definitions, and cadence differ by source. GIDS does not replace WHO, CDC, or national public-health guidance.",
            "zh": "官方事件源可追溯但并非穷尽；各来源的报告延迟、定义和频率不同。GIDS 不替代 WHO、CDC 或各国公共卫生机构的指导。",
        },
        # Retained in the runtime/history databases for reproducibility. Site
        # accessors strip private keys before any static/public export.
        "_analysis_ledger": _json_safe(analysis_ledger or []),
    }
    payload["narrative"] = _deterministic_narrative(checked_at.strftime("%Y-%m"), coverage)
    payload = _json_safe(payload)
    content_freshness = {
        source: {
            key: value
            for key, value in (health or {}).items()
            if key not in {"checked_at", "error"}
        }
        for source, health in freshness.items()
    }
    content = {
        "method_version": payload["method_version"],
        "data_through": payload["data_through"],
        "coverage": coverage,
        "analysis_execution": payload["analysis_execution"],
        "freshness": content_freshness,
        "increasing": payload["increasing"],
        "respiratory": payload["respiratory"],
        "emerging": payload["emerging"],
        "unusual": payload["unusual"],
    }
    payload["input_hash"] = _text_hash(json.dumps(content, sort_keys=True, default=str))
    return payload


async def _prior_analyzed_counts(limit: int = 7) -> list[int]:
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily", SituationSnapshot.quality_gate_status == "passed")
                .order_by(SituationSnapshot.checked_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [int((row.payload or {}).get("coverage", {}).get("analyzed_series_count") or 0) for row in reversed(rows)]


async def _latest_source_health() -> dict[str, Any]:
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return dict((row.payload or {}).get("freshness") or {}) if row else {}


async def persist_snapshot(
    payload: dict[str, Any],
    *,
    snapshot_kind: str = "daily",
    period_key: str | None = None,
    daily_retention_days: int = 90,
) -> SituationSnapshot:
    period = period_key or payload.get("period_key") or str(payload["checked_at"])[:10]
    checked_at = str(payload["checked_at"])
    async with get_db() as db:
        existing_rows = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == snapshot_kind, SituationSnapshot.period_key == period)
                .order_by(SituationSnapshot.revision.desc())
            )
        ).scalars().all()
        latest = existing_rows[0] if existing_rows else None
        if latest is not None and latest.input_hash == payload["input_hash"]:
            latest.checked_at = checked_at
            latest.quality_gate_status = payload["quality_gate"]["status"]
            latest.quality_gate = payload["quality_gate"]
            stored = dict(latest.payload or {})
            stored["checked_at"] = checked_at
            stored["freshness"] = payload.get("freshness") or {}
            stored["quality_gate"] = payload["quality_gate"]
            stored["quality_gate_status"] = payload["quality_gate"]["status"]
            latest.payload = stored
            return latest

        revision = (latest.revision + 1) if latest else 1
        content_updated_at = checked_at
        row_payload = dict(payload)
        row_payload.update(
            {
                "snapshot_kind": snapshot_kind,
                "period_key": period,
                "revision": revision,
                "supersedes_snapshot_id": latest.snapshot_id if latest else None,
                "content_updated_at": content_updated_at,
            }
        )
        snapshot_id = f"situation-{snapshot_kind}-{period}-r{revision}"
        row_payload["snapshot_id"] = snapshot_id
        row = SituationSnapshot(
            snapshot_id=snapshot_id,
            snapshot_kind=snapshot_kind,
            period_key=period,
            iso_week=period if snapshot_kind == "weekly" else payload.get("iso_week"),
            generated_at=checked_at,
            checked_at=checked_at,
            content_updated_at=content_updated_at,
            data_through=payload.get("data_through"),
            method_version=payload["method_version"],
            input_hash=payload["input_hash"],
            status="published" if payload["quality_gate"]["passed"] else "quality_failed",
            quality_gate_status=payload["quality_gate"]["status"],
            quality_gate=payload["quality_gate"],
            revision=revision,
            supersedes_snapshot_id=latest.snapshot_id if latest else None,
            payload=row_payload,
        )
        db.add(row)
        if snapshot_kind == "daily":
            cutoff = utc_now() - timedelta(days=daily_retention_days)
            await db.execute(
                delete(SituationSnapshot).where(
                    SituationSnapshot.snapshot_kind == "daily",
                    SituationSnapshot.created_at < cutoff,
                )
            )
        return row


async def refresh_situation(*, fetch_events: bool = True, now: datetime | None = None) -> dict[str, Any]:
    await init_database()
    config = load_config()
    checked_at = now or utc_now()
    source_health: dict[str, Any] = await _latest_source_health() if not fetch_events else {}
    respiratory_cards: list[dict[str, Any]] = []
    if fetch_events:
        event_rows, event_health = await fetch_external_events(config)
        await persist_events(event_rows)
        cdc_frame, respiratory_cards, cdc_health = await fetch_cdc_respiratory_history()
        if not cdc_frame.empty:
            persistence = await persist_cdc_respiratory_series(cdc_frame)
            for details in cdc_health.values():
                details["persistence"] = persistence
        source_health.update(event_health)
        source_health.update(cdc_health)
    frame = await fetch_series_frame(config)
    assessments, rejected_reasons, analysis_ledger = evaluate_frame_with_ledger(
        frame, config, as_of=checked_at.date()
    )
    events = await published_events(
        source_health=source_health if fetch_events else None,
        stale_hours=int(config.get("event_stale_hours", 72)),
    )
    assessments = apply_composite_risk(assessments, events, config)
    final_by_id = {row.get("id"): row for row in assessments}
    for ledger_row in analysis_ledger:
        final = final_by_id.get(ledger_row.get("assessment_id"))
        if final is not None:
            ledger_row["risk"] = final.get("risk") or {}
    events = attach_event_usage(events, assessments)
    analysis_summary = summarize_analysis_ledger(analysis_ledger)
    analysis_summary["official_events"] = {
        "available_count": len(events),
        "used_in_composite_risk_count": sum(
            1
            for event in events
            if (event.get("usage") or {}).get("status") == "used_in_composite_risk"
        ),
        "event_evidence_only_count": sum(
            1
            for event in events
            if (event.get("usage") or {}).get("status") == "event_evidence_only"
        ),
    }
    signals = [row for row in assessments if row.get("candidate")]
    payload = build_snapshot(
        signals,
        events,
        source_health,
        config,
        checked_at,
        assessments=assessments,
        respiratory_cards=respiratory_cards,
        rejected_reasons=rejected_reasons,
        source_series_count=int(frame["series_code"].nunique()) if not frame.empty else 0,
        analysis_summary=analysis_summary,
        analysis_ledger=analysis_ledger,
    )
    payload["quality_gate"] = evaluate_quality_gate(
        payload,
        prior_analyzed_counts=await _prior_analyzed_counts(),
        require_algorithm_execution=True,
        required_analyzed_sources=config.get("quality", {}).get(
            "required_analyzed_source_systems", []
        ),
    )
    payload["quality_gate_status"] = payload["quality_gate"]["status"]
    daily = await persist_snapshot(payload, snapshot_kind="daily", period_key=checked_at.date().isoformat(), daily_retention_days=int(config.get("daily_retention_days", 90)))
    week = checked_at.isocalendar()
    weekly = await persist_snapshot(payload, snapshot_kind="weekly", period_key=f"{week.year}-W{week.week:02d}")
    monthly = await persist_snapshot(payload, snapshot_kind="monthly", period_key=checked_at.strftime("%Y-%m"))
    # History persistence is part of the release contract. A history-store
    # failure aborts the refresh so the release pipeline keeps the last good
    # public version instead of publishing without durable revision evidence.
    for snapshot in (daily, weekly, monthly):
        await archive_snapshot(snapshot)
    return dict(daily.payload)


async def latest_snapshot() -> dict[str, Any] | None:
    async with get_db() as db:
        row = (
            await db.execute(
                select(SituationSnapshot)
                .where(
                    SituationSnapshot.status == "published",
                    SituationSnapshot.snapshot_kind == "daily",
                    SituationSnapshot.quality_gate_status == "passed",
                )
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().first()
    return _public_payload(dict(row.payload)) if row else None


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not key.startswith("_")}


async def _archive_snapshots(kind: str) -> list[dict[str, Any]]:
    async with get_db() as db:
        rows = (
            await db.execute(
                select(SituationSnapshot)
                .where(
                    SituationSnapshot.snapshot_kind == kind,
                    SituationSnapshot.status == "published",
                    SituationSnapshot.quality_gate_status == "passed",
                )
                .order_by(SituationSnapshot.period_key.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().all()
    latest_by_period: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest_by_period.setdefault(row.period_key, _public_payload(dict(row.payload)))
    return list(latest_by_period.values())


async def weekly_snapshots() -> list[dict[str, Any]]:
    return await _archive_snapshots("weekly")


async def monthly_snapshots() -> list[dict[str, Any]]:
    return await _archive_snapshots("monthly")


__all__ = [
    "METHOD_VERSION",
    "analyze_frame",
    "analyze_series",
    "apply_composite_risk",
    "build_snapshot",
    "fetch_series_frame",
    "latest_snapshot",
    "load_config",
    "monthly_snapshots",
    "persist_snapshot",
    "refresh_situation",
    "weekly_snapshots",
]
