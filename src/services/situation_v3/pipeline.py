"""End-to-end Situation Room v3 acquisition, analysis, and publication."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from src.core.database import init_database
from src.services.situation_events import (
    fetch_cdc_respiratory_history,
    fetch_external_events,
    persist_cdc_respiratory_series,
    persist_events,
    published_events,
    source_adapter_registry,
)
from src.services.situation_room import load_config

from .contracts import SituationReportV3
from .model import evaluate_frame_v3
from .persistence import (
    active_signal_ids_before_period_v3,
    daily_reports_for_period_v3,
    event_review_states_v3,
    mark_analysis_run_failed_v3,
    publish_report_v3,
    stage_analysis_run_v3,
    signal_review_states_v3,
    stable_event_cluster_ids_v3,
)
from .reporting import (
    METHOD_VERSION,
    build_daily_report_v3,
    build_period_report_v3,
    cluster_official_events,
)
from .source_adapters import fetch_series_inputs_v3


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _analysis_input_hash(
    *,
    frame: Any,
    sql_rejection_ledger: list[dict[str, Any]],
    events: list[dict[str, Any]],
    respiratory_cards: list[dict[str, Any]],
    source_health: dict[str, Any],
    config: dict[str, Any],
) -> str:
    """Fingerprint acquired facts and analysis configuration, not report output."""

    frame_payload = frame.to_json(
        orient="split",
        date_format="iso",
        date_unit="us",
        double_precision=15,
        default_handler=str,
    )
    manifest = {
        "frame_sha256": hashlib.sha256(frame_payload.encode()).hexdigest(),
        "frame_rows": int(len(frame)),
        "sql_rejection_ledger": sql_rejection_ledger,
        "events": events,
        "respiratory_cards": respiratory_cards,
        "source_health": source_health,
        "config_hash": _config_hash(config),
    }
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _analysis_run_id(checked_at: datetime, input_hash: str) -> str:
    """Make concurrent runs distinct while retaining deterministic retries."""

    return (
        "situation-v3-run-"
        + checked_at.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + input_hash[:10]
    )


def _previous_iso_week(day: date) -> tuple[str, date, date]:
    current_monday = day - timedelta(days=day.weekday())
    end = current_monday - timedelta(days=1)
    start = end - timedelta(days=6)
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start, end


def _previous_month(day: date) -> tuple[str, date, date]:
    first = day.replace(day=1)
    end = first - timedelta(days=1)
    start = end.replace(day=1)
    return start.strftime("%Y-%m"), start, end


async def _publish_closed_period(
    *,
    kind: str,
    period_key: str,
    start: date,
    end: date,
    as_of: datetime,
) -> SituationReportV3 | None:
    members = await daily_reports_for_period_v3(start, end)
    if not members:
        return None
    run_ids = [run_id for run_id, _ in members if run_id]
    previous_active = await active_signal_ids_before_period_v3(kind, start)
    report = build_period_report_v3(
        [daily for _, daily in members],
        kind=kind,
        period_key=period_key,
        period_start=start,
        period_end=end,
        as_of=as_of,
        previous_active_signal_ids=previous_active,
    )
    published, _ = await publish_report_v3(
        report,
        run_ids=run_ids,
        channel=f"{kind}-latest",
    )
    return published


async def refresh_situation_v3(
    *,
    fetch_events: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    await init_database()
    checked_at = now or utc_now()
    checked_at = checked_at if checked_at.tzinfo else checked_at.replace(tzinfo=timezone.utc)
    config = load_config()
    timings: dict[str, float] = {}
    source_health: dict[str, Any] = {}
    respiratory_cards: list[dict[str, Any]] = []
    acquisition_started = perf_counter()
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
    else:
        skipped_at = checked_at.isoformat()
        source_health = {
            str(adapter["health_key"]): {
                "status": "not_checked",
                "checked_at": skipped_at,
                "url": adapter.get("url"),
                "error": "External acquisition was disabled for this analysis run",
            }
            for adapter in source_adapter_registry()
            if adapter.get("health_key")
        }
    timings["source_acquisition_seconds"] = round(perf_counter() - acquisition_started, 4)
    fetch_started = perf_counter()
    frame, sql_rejection_ledger, registered_series_count = await fetch_series_inputs_v3(
        config,
        as_of=checked_at.date(),
    )
    timings["series_fetch_seconds"] = round(perf_counter() - fetch_started, 4)
    model_started = perf_counter()
    signals, rejected, ledger = evaluate_frame_v3(frame, config, as_of=checked_at.date())
    ledger = [*sql_rejection_ledger, *ledger]
    for item in sql_rejection_ledger:
        reason = str(item.get("rejection_reason") or "unknown")
        rejected[reason] = rejected.get(reason, 0) + 1
    timings["model_seconds"] = round(perf_counter() - model_started, 4)
    events = await published_events(
        source_health=source_health,
        stale_hours=int(config.get("event_stale_hours", 72)),
    )
    provisional_clusters = cluster_official_events(events)
    event_cluster_ids = await stable_event_cluster_ids_v3(provisional_clusters)
    event_reviews = await event_review_states_v3(
        event_cluster_ids.get(cluster.cluster_id, cluster.cluster_id)
        for cluster in provisional_clusters
    )
    signal_reviews = await signal_review_states_v3(
        signal.identity.signal_id for signal in signals
    )
    report = build_daily_report_v3(
        signals=signals,
        ledger=ledger,
        rejected_reasons=rejected,
        events=events,
        respiratory_cards=respiratory_cards,
        freshness=source_health,
        config=config,
        checked_at=checked_at,
        registered_series_count=registered_series_count,
        event_reviews=event_reviews,
        signal_reviews=signal_reviews,
        event_cluster_ids=event_cluster_ids,
    )
    input_hash = _analysis_input_hash(
        frame=frame,
        sql_rejection_ledger=sql_rejection_ledger,
        events=events,
        respiratory_cards=respiratory_cards,
        source_health=source_health,
        config=config,
    )
    run_id = _analysis_run_id(checked_at, input_hash)
    await stage_analysis_run_v3(
        run_id=run_id,
        checked_at=checked_at,
        method_version=METHOD_VERSION,
        config_hash=_config_hash(config),
        input_hash=input_hash,
        signals=signals,
        ledger=ledger,
        timings=timings,
        coverage=report.coverage.model_dump(mode="json"),
    )
    try:
        report, changed = await publish_report_v3(report, run_ids=[run_id], channel="latest")
    except Exception as exc:
        await mark_analysis_run_failed_v3(run_id, exc)
        raise
    period_reports: dict[str, str] = {}
    if report.quality_gate.passed:
        weekly_key, weekly_start, weekly_end = _previous_iso_week(checked_at.date())
        weekly = await _publish_closed_period(
            kind="weekly",
            period_key=weekly_key,
            start=weekly_start,
            end=weekly_end,
            as_of=checked_at,
        )
        if weekly:
            period_reports["weekly"] = weekly.report.report_id
        month_key, month_start, month_end = _previous_month(checked_at.date())
        monthly = await _publish_closed_period(
            kind="monthly",
            period_key=month_key,
            start=month_start,
            end=month_end,
            as_of=checked_at,
        )
        if monthly:
            period_reports["monthly"] = monthly.report.report_id
    return {
        "report": report.model_dump(mode="json"),
        "run_id": run_id,
        "published_changed": changed,
        "period_reports": period_reports,
        "timings": timings,
    }


__all__ = ["refresh_situation_v3"]
