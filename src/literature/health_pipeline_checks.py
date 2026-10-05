"""Ingestion, checkpoint, and metadata-backfill health checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain import TaskType

from .health_check_utils import _check
from .health_snapshot import (
    _ACTIVE_TASK_STATUSES,
    HealthThresholds,
    ResearchRadarSnapshot,
    _age_hours,
    _enum_value,
    _safe_int,
    _source_run_outcome,
)
from .metadata_backfill import SUPPORTED_PROVIDERS


def _is_core_run(run: Mapping[str, Any]) -> bool:
    return "crossref" in str(run.get("source") or "").lower().split("+")


def _pipeline_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    now = snapshot.collected_at
    core_runs = [run for run in snapshot.ingest_runs if _is_core_run(run)]
    terminal = [run for run in core_runs if _enum_value(run.get("status")) in {"completed", "failed"}]
    completed = [run for run in core_runs if _enum_value(run.get("status")) == "completed"]
    newest_terminal = terminal[0] if terminal else None
    latest_completed = completed[0] if completed else None
    consecutive_failures = 0
    for run in terminal:
        if _enum_value(run.get("status")) != "failed":
            break
        consecutive_failures += 1
    recovered_failures = 0
    if newest_terminal and _enum_value(newest_terminal.get("status")) == "completed":
        for run in terminal[1:]:
            if _enum_value(run.get("status")) != "failed":
                break
            recovered_failures += 1
    stale_running = sum(
        1 for run in core_runs
        if _enum_value(run.get("status")) == "running"
        and (_age_hours(now, run.get("started_at")) or 0.0) * 60 > thresholds.max_stale_run_minutes
    )
    completed_age = _age_hours(now, latest_completed.get("completed_at")) if latest_completed else None
    source_lag = _age_hours(now, latest_completed.get("through_indexed_at")) if latest_completed else None
    freshness_ok = (
        completed_age is not None
        and completed_age <= thresholds.max_sync_age_hours
        and source_lag is not None
        and source_lag <= thresholds.max_source_lag_hours
    )
    checks = [_check(
        "sync_freshness",
        "pass" if freshness_ok else "critical",
        {
            "completed_run_present": latest_completed is not None,
            "completed_run_age_hours": completed_age,
            "source_watermark_lag_hours": source_lag,
        },
        {
            "max_sync_age_hours": thresholds.max_sync_age_hours,
            "max_source_lag_hours": thresholds.max_source_lag_hours,
        },
    )]
    newest_failed = bool(newest_terminal and _enum_value(newest_terminal.get("status")) == "failed")
    failure_status = "critical" if (
        consecutive_failures > thresholds.max_consecutive_failures
        or stale_running > 0
    ) else ("warning" if newest_failed or recovered_failures else "pass")
    checks.append(_check(
        "sync_failures_and_recovery",
        failure_status,
        {
            "latest_terminal_status": (
                _enum_value(newest_terminal.get("status")) if newest_terminal else "missing"
            ),
            "consecutive_failures": consecutive_failures,
            "recovered_failures": recovered_failures,
            "stale_running_runs": stale_running,
        },
        {
            "max_consecutive_failures": thresholds.max_consecutive_failures,
            "max_stale_run_minutes": thresholds.max_stale_run_minutes,
        },
        next_action_code=(
            "reconcile_stale_ingest_runs_dry_run"
            if stale_running > 0
            else "inspect_sync_failures_and_recovery"
            if failure_status != "pass"
            else "none"
        ),
    ))

    checkpoint = latest_completed.get("checkpoint") if latest_completed else None
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    latest_counts = latest_completed.get("counts") if latest_completed else None
    latest_counts = latest_counts if isinstance(latest_counts, Mapping) else {}
    checkpoint_valid = bool(checkpoint.get("strategy"))
    truncated = bool(checkpoint.get("truncated"))
    resumable = not truncated or bool(
        checkpoint.get("next_from_indexed_at")
        or isinstance(checkpoint.get("resume_after"), Mapping)
    )
    catch_up_required = bool(
        checkpoint.get("catch_up_required")
        or latest_counts.get("source_catch_up_required")
    )
    checks.append(_check(
        "sync_checkpoint",
        "pass" if checkpoint_valid and resumable else "critical",
        {
            "present": bool(checkpoint),
            "strategy_present": bool(checkpoint.get("strategy")),
            "truncated": truncated,
            "truncated_checkpoint_resumable": resumable,
            "catch_up_required": catch_up_required,
            "remaining_index_span_seconds": _safe_int(
                checkpoint.get("remaining_index_span_seconds")
                or latest_counts.get("source_remaining_index_span_seconds")
            ),
            "records_prefetched": _safe_int(
                checkpoint.get("records_prefetched")
                or latest_counts.get("source_records_prefetched")
            ),
            "records_returned": _safe_int(
                checkpoint.get("records_returned")
                or latest_counts.get("source_records_returned")
            ),
            "lookahead_records": _safe_int(
                checkpoint.get("lookahead_records")
                or latest_counts.get("source_lookahead_records")
            ),
            "pages_fetched": _safe_int(
                checkpoint.get("pages_fetched")
                or latest_counts.get("source_pages_fetched")
            ),
            "fetch_efficiency_ratio": checkpoint.get("fetch_efficiency_ratio"),
            "nested_checkpoint_count": sum(
                isinstance(checkpoint.get(name), Mapping)
                for name in ("rss", "controlled_discovery", "official_guidance")
            ),
        },
    ))

    latest_sync_task = next(
        (
            task for task in snapshot.tasks
            if _enum_value(task.get("type")) == TaskType.SYNC_LITERATURE.value
        ),
        None,
    )
    catch_up = (
        latest_sync_task.get("catch_up")
        if isinstance(latest_sync_task, Mapping)
        and isinstance(latest_sync_task.get("catch_up"), Mapping)
        else {}
    )
    orchestration_status = str(catch_up.get("catch_up_status") or "unknown")
    latest_task_status = _enum_value(
        latest_sync_task.get("status") if latest_sync_task else None
    )
    if not catch_up_required:
        catch_up_health = "pass"
        catch_up_next_action = "none"
    elif latest_task_status in _ACTIVE_TASK_STATUSES:
        catch_up_health = "pass"
        catch_up_next_action = "await_active_literature_sync"
    elif orchestration_status in {"scheduled", "already_scheduled"}:
        catch_up_health = "pass"
        catch_up_next_action = "await_accelerated_catch_up"
    elif orchestration_status == "paused_backpressure":
        catch_up_health = "warning"
        catch_up_next_action = "reduce_exception_backlog_below_resume_threshold"
    elif orchestration_status == "paused_backlog_measurement":
        catch_up_health = "critical"
        catch_up_next_action = "retry_backlog_measurement"
    elif orchestration_status == "schedule_persistence_unavailable":
        catch_up_health = "critical"
        catch_up_next_action = "inspect_scheduler_persistence"
    elif orchestration_status == "disabled":
        catch_up_health = "warning"
        catch_up_next_action = "enable_accelerated_catch_up"
    elif orchestration_status == "waiting_for_scheduled_trigger":
        catch_up_health = "warning"
        catch_up_next_action = "await_next_scheduled_sync"
    else:
        catch_up_health = "warning"
        catch_up_next_action = "inspect_latest_sync_result"
    checks.append(_check(
        "catch_up_orchestration",
        catch_up_health,
        {
            "catch_up_required": catch_up_required,
            "latest_sync_task_status": latest_task_status or "missing",
            "orchestration_status": orchestration_status,
            "next_run_at": catch_up.get("catch_up_next_run_at"),
            "backlog_observed_count": catch_up.get("catch_up_backlog_observed_count"),
            "backlog_projected_upper_bound": catch_up.get(
                "catch_up_backlog_projected_upper_bound"
            ),
            "backlog_limit": catch_up.get("catch_up_backlog_limit"),
            "resume_below_backlog": catch_up.get("catch_up_resume_below_backlog"),
            "required_backlog_reduction": catch_up.get(
                "catch_up_required_backlog_reduction"
            ),
            "pause_reason": catch_up.get("catch_up_backpressure_reason"),
        },
        next_action_code=catch_up_next_action,
    ))

    completed_sources = (
        {
            source.strip().lower()
            for source in str(latest_completed.get("source") or "").split("+")
            if source.strip()
        }
        if latest_completed
        else set()
    )
    source_results = [
        _source_run_outcome(
            source,
            completed_sources=completed_sources,
            counts=latest_counts,
        )
        for source in snapshot.expected_sources
    ]
    successful_sources = [
        result for result in source_results if result["status"] == "success"
    ]
    unsuccessful_sources = [
        result for result in source_results if result["status"] != "success"
    ]
    checks.append(_check(
        "enabled_source_success",
        "pass" if not unsuccessful_sources else "warning",
        {
            "expected_source_count": len(snapshot.expected_sources),
            "successful_source_count": len(successful_sources),
            "unsuccessful_source_count": len(unsuccessful_sources),
            "missing_source_count": sum(
                result["reason"] == "not_in_latest_completed_run"
                for result in source_results
            ),
            "provider_error_source_count": sum(
                result["reason"] == "provider_errors" for result in source_results
            ),
            "credential_skipped_source_count": sum(
                result["reason"] == "skipped_credentials" for result in source_results
            ),
            "not_attempted_source_count": sum(
                result["reason"] == "not_attempted" for result in source_results
            ),
            "count_contract_failure_source_count": sum(
                result["reason"] in {
                    "missing_count_contract",
                    "invalid_count_contract",
                    "unknown_count_contract",
                }
                for result in source_results
            ),
            "source_results": source_results,
        },
        {"required_unsuccessful_source_count": 0},
    ))
    return checks


def _backfill_check(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> dict[str, Any]:
    checkpoint = snapshot.backfill_checkpoint or {}
    status = str(checkpoint.get("status") or "missing")
    age = _age_hours(snapshot.collected_at, checkpoint.get("updated_at"))
    failures = _safe_int((checkpoint.get("run_stats") or {}).get("failure_count"))
    provider_stats = checkpoint.get("run_stats", {}).get("provider_stats", {})
    coverage = checkpoint.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    provider_failed = sum(
        _safe_int(value.get("failed"))
        for value in provider_stats.values()
        if isinstance(value, Mapping)
    ) if isinstance(provider_stats, Mapping) else 0
    completed = status in {"completed", "completed_at_limit"}
    stalled = status == "running" and (
        age is None or age > thresholds.max_backfill_stalled_hours
    )
    if snapshot.backfill_read_status != "ok":
        health_status = "warning"
    elif failures or provider_failed or status == "stopped_on_provider_error" or stalled:
        health_status = "critical"
    elif completed or status == "running":
        health_status = "pass"
    else:
        health_status = "warning"
    if snapshot.backfill_read_status != "ok":
        next_action = "run_metadata_backfill_dry_run"
    elif failures or provider_failed or status == "stopped_on_provider_error":
        next_action = "retry_failed_provider_batch"
    elif stalled:
        next_action = "resume_stalled_metadata_backfill"
    elif status == "completed_below_target":
        next_action = "review_provider_match_gap"
    else:
        next_action = "none"
    return _check(
        "metadata_backfill_checkpoint",
        health_status,
        {
            "read_status": snapshot.backfill_read_status,
            "status": status,
            "age_hours": age,
            "failure_count": failures,
            "provider_failed_records": provider_failed,
            "provider_count": len(provider_stats) if isinstance(provider_stats, Mapping) else 0,
            "target_reached": bool(checkpoint.get("target_reached")) if coverage else None,
            "provider_deficits": {
                provider: _safe_int(coverage.get(provider, {}).get("deficit"))
                for provider in SUPPORTED_PROVIDERS
                if isinstance(coverage.get(provider), Mapping)
            },
        },
        {"max_stalled_hours": thresholds.max_backfill_stalled_hours, "max_failures": 0},
        next_action_code=next_action,
    )
