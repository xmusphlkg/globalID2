"""Task, editorial-backlog, and automation health checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain import TaskType

from .health_check_utils import _check
from .health_snapshot import (
    _ACTIVE_TASK_STATUSES,
    _CORE_TASK_TYPES,
    HealthThresholds,
    ResearchRadarSnapshot,
    _age_hours,
    _enum_value,
    _safe_int,
)


def _operations_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    now = snapshot.collected_at
    latest_by_type: dict[str, Mapping[str, Any]] = {}
    for task in snapshot.tasks:
        kind = _enum_value(task.get("type"))
        if kind in _CORE_TASK_TYPES and kind not in latest_by_type:
            latest_by_type[kind] = task
    latest_failed = sum(
        _enum_value(task.get("status")) == "failed" for task in latest_by_type.values()
    )
    active = [task for task in snapshot.tasks if _enum_value(task.get("status")) in _ACTIVE_TASK_STATUSES]
    stale_active = sum(
        ((_age_hours(now, task.get("started_at") or task.get("updated_at") or task.get("created_at")) or 0.0) * 60)
        > thresholds.max_stale_task_minutes
        for task in active
    )
    recent_failures = sum(_enum_value(task.get("status")) == "failed" for task in snapshot.tasks)
    recovered_types = 0
    for kind, latest in latest_by_type.items():
        if _enum_value(latest.get("status")) != "completed":
            continue
        same_kind = [task for task in snapshot.tasks if _enum_value(task.get("type")) == kind]
        if any(_enum_value(task.get("status")) == "failed" for task in same_kind[1:]):
            recovered_types += 1
    latest_enrichment = latest_by_type.get(TaskType.ENRICH_LITERATURE.value)
    latest_enrichment_counts = (
        latest_enrichment.get("enrichment")
        if isinstance(latest_enrichment, Mapping)
        and isinstance(latest_enrichment.get("enrichment"), Mapping)
        else {}
    )
    latest_enrichment_failed = _safe_int(latest_enrichment_counts.get("failed"))
    completed_with_enrichment_failures = int(
        bool(
            latest_enrichment
            and _enum_value(latest_enrichment.get("status")) == "completed"
            and latest_enrichment_failed > 0
        )
    )
    task_status = "critical" if (
        latest_failed > thresholds.max_latest_failed_task_types or stale_active
    ) else (
        "warning" if latest_failed or recovered_types or completed_with_enrichment_failures else "pass"
    )

    autopilot = next(
        (run for run in snapshot.ingest_runs if str(run.get("source") or "") == "research-radar-autopilot"),
        None,
    )
    autopilot_counts = (autopilot or {}).get("counts") or {}
    raw_article_exceptions = _safe_int(autopilot_counts.get("article_exceptions"))
    raw_link_exceptions = _safe_int(autopilot_counts.get("link_exceptions"))
    raw_summary_exceptions = _safe_int(autopilot_counts.get("summary_exceptions"))
    raw_articles_deferred = _safe_int(autopilot_counts.get("articles_deferred"))
    raw_summaries_deferred = _safe_int(autopilot_counts.get("summaries_deferred"))
    raw_summaries_archived = _safe_int(autopilot_counts.get("summaries_archived"))
    automation_exceptions = (
        raw_article_exceptions + raw_link_exceptions + raw_summary_exceptions
    )

    def explicit_non_actionable_decision(metadata: Any) -> str | None:
        if not isinstance(metadata, Mapping):
            return None
        autopilot = metadata.get("autopilot")
        if not isinstance(autopilot, Mapping):
            return None
        decision = autopilot.get("decision")
        return decision if decision in {"defer", "archive"} else None

    raw_review_articles = [
        row for row in snapshot.articles
        if str(row.get("publication_status") or "") == "review"
    ]
    article_non_actionable_decisions = [
        explicit_non_actionable_decision(row.get("metadata_"))
        for row in raw_review_articles
    ]
    deferred_review_articles = article_non_actionable_decisions.count("defer")
    archived_decision_review_articles = article_non_actionable_decisions.count("archive")
    review_articles = (
        len(raw_review_articles)
        - deferred_review_articles
        - archived_decision_review_articles
    )
    review_links = _safe_int(snapshot.current_review_link_count)
    raw_review_summaries = [
        row for row in snapshot.summaries if str(row.get("status") or "") == "review"
    ]
    summary_non_actionable_decisions = [
        explicit_non_actionable_decision(row.get("generation_metadata"))
        for row in raw_review_summaries
    ]
    deferred_review_summaries = summary_non_actionable_decisions.count("defer")
    archived_decision_review_summaries = summary_non_actionable_decisions.count("archive")
    review_summaries = (
        len(raw_review_summaries)
        - deferred_review_summaries
        - archived_decision_review_summaries
    )
    archived_summaries = sum(
        str(row.get("status") or "") == "archived" for row in snapshot.summaries
    )
    active_gap_statuses = {"open", "searching", "review", "no_results", "error"}
    active_gap_errors = sum(
        str(row.get("status") or "") in active_gap_statuses
        and (bool(row.get("error")) or str(row.get("status") or "") == "error")
        for row in snapshot.evidence_gaps
    )
    retained_gap_errors = sum(bool(row.get("error")) for row in snapshot.evidence_gaps)
    open_gaps = sum(str(row.get("status") or "") == "open" for row in snapshot.evidence_gaps)
    # The latest autopilot counters are a point-in-time audit snapshot.  In
    # particular, ``article_exceptions`` is the same population that remains
    # in the article table with publication_status=review, so adding both
    # deterministically double-counts unresolved articles.  Threshold current
    # review objects, excluding only exact persisted ``autopilot.decision``
    # values of ``defer`` or ``archive``. Missing, malformed, or unknown
    # metadata remains fail-closed in the backlog. Archived summary rows are
    # already outside the review population. Keep raw counts for diagnosis.
    exception_backlog = review_articles + review_links + review_summaries
    raw_legacy_combined = automation_exceptions + len(raw_review_articles)
    exception_status = "critical" if (
        exception_backlog > thresholds.max_exception_backlog
        or active_gap_errors > thresholds.max_evidence_gap_errors
    ) else "pass"
    return [
        _check(
            "background_tasks",
            task_status,
            {
                "task_types_observed": len(latest_by_type),
                "active_task_count": len(active),
                "stale_active_task_count": stale_active,
                "latest_failed_task_types": latest_failed,
                "recent_failed_task_count": recent_failures,
                "recovered_task_types": recovered_types,
                "latest_enrichment_failed_summaries": latest_enrichment_failed,
                "completed_with_enrichment_failures": completed_with_enrichment_failures,
            },
            {
                "max_stale_task_minutes": thresholds.max_stale_task_minutes,
                "max_latest_failed_task_types": thresholds.max_latest_failed_task_types,
            },
            next_action_code=(
                "inspect_enrichment_generation_failures"
                if completed_with_enrichment_failures
                else "inspect_background_tasks"
                if task_status != "pass"
                else "none"
            ),
        ),
        _check(
            "exception_backlog",
            exception_status,
            {
                "autopilot_snapshot_present": autopilot is not None,
                "automation_exception_count": automation_exceptions,
                "review_article_count": len(raw_review_articles),
                "raw_latest_autopilot_article_exception_count": raw_article_exceptions,
                "raw_latest_autopilot_link_exception_count": raw_link_exceptions,
                "raw_latest_autopilot_summary_exception_count": raw_summary_exceptions,
                "raw_latest_autopilot_article_deferred_count": raw_articles_deferred,
                "raw_latest_autopilot_summary_deferred_count": raw_summaries_deferred,
                "raw_latest_autopilot_summary_archived_count": raw_summaries_archived,
                "raw_legacy_combined_exception_backlog": raw_legacy_combined,
                "raw_review_article_count": len(raw_review_articles),
                "raw_review_summary_count": len(raw_review_summaries),
                "deferred_review_article_count": deferred_review_articles,
                "deferred_review_summary_count": deferred_review_summaries,
                "deferred_review_object_count": (
                    deferred_review_articles + deferred_review_summaries
                ),
                "archived_decision_review_article_count": (
                    archived_decision_review_articles
                ),
                "archived_decision_review_summary_count": (
                    archived_decision_review_summaries
                ),
                "archived_decision_review_object_count": (
                    archived_decision_review_articles
                    + archived_decision_review_summaries
                ),
                "archived_summary_count": archived_summaries,
                "current_review_article_count": review_articles,
                "current_review_link_count": review_links,
                "current_review_summary_count": review_summaries,
                "backlog_counting_basis": "current_actionable_review_objects",
                "uniqueish_exception_backlog": exception_backlog,
                "combined_exception_backlog": exception_backlog,
                "open_evidence_gap_count": open_gaps,
                "active_evidence_gap_error_count": active_gap_errors,
                "retained_evidence_gap_error_count": retained_gap_errors,
            },
            {
                "max_exception_backlog": thresholds.max_exception_backlog,
                "max_evidence_gap_errors": thresholds.max_evidence_gap_errors,
            },
            next_action_code=(
                "run_literature_autopilot_dry_run" if exception_status != "pass" else "none"
            ),
        ),
    ]
