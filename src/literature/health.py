"""Read-only, privacy-safe health evaluation for Research Radar.

The collector issues SELECT statements and reads generated JSON artifacts.  The
evaluator deliberately returns aggregate counters and stable reason codes only:
article identifiers, task UUIDs, exception text, source URLs, and database
connection details never cross the health-report boundary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import and_, case, func, select

from src.domain import (
    LiteratureArticle,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    LiteratureSummary,
    Task,
    TaskStatus,
    TaskType,
)

from .classification import CLASSIFICATION_VERSION
from .metadata_backfill import DEFAULT_CHECKPOINT_PATH, SUPPORTED_PROVIDERS
from .release_validation import validate_public_research_payload
from .weekly_briefs import project_weekly_editorial_review
from .weekly_ai_review import project_weekly_ai_review


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE_PATH = ROOT / "astro-site/src/data/research/index.json"
REPORT_SCHEMA_VERSION = 1
_MAX_RELEASE_BYTES = 64 * 1024 * 1024
_MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
_CORE_TASK_TYPES = {
    TaskType.SYNC_LITERATURE.value,
    TaskType.ENRICH_LITERATURE.value,
    TaskType.DISCOVER_LITERATURE_GAPS.value,
}
_ACTIVE_TASK_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.QUEUED.value,
    TaskStatus.RUNNING.value,
    TaskStatus.RETRYING.value,
}

# A source name in ``LiteratureIngestRun.source`` records configuration intent,
# not execution success.  These are the bounded, aggregate result fields emitted
# by ``LiteraturePipeline.execute`` for each enabled provider.  An explicit zero
# is valid evidence that a provider ran and found no matching records.
_SOURCE_RESULT_COUNT_CONTRACTS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "crossref": {
        "result": ("crossref_fetched", "source_records_seen", "source_records_returned"),
        "error": (),
        "skipped": (),
    },
    "europe-pmc": {
        "result": ("europe_pmc_enriched",),
        "error": ("europe_pmc_errors",),
        "skipped": (),
    },
    "openalex": {
        "result": ("openalex_enriched",),
        "error": ("openalex_errors",),
        "skipped": (),
    },
    "unpaywall": {
        "result": ("unpaywall_enriched",),
        "error": ("unpaywall_errors",),
        "skipped": (),
    },
    "publisher-rss": {
        "result": (
            "publisher_rss_fetched",
            "publisher_rss_records_seen",
            "publisher_rss_feeds_modified",
            "publisher_rss_feeds_not_modified",
        ),
        "error": ("publisher_rss_feed_errors",),
        "skipped": (),
        # Zero article records is healthy, but at least one configured feed must
        # have returned either a modified or not-modified response.
        "attempt_any": (
            "publisher_rss_feeds_modified",
            "publisher_rss_feeds_not_modified",
        ),
    },
    "springer-nature": {
        "result": ("springer_nature_fetched",),
        "error": ("springer_nature_errors",),
        "skipped": ("springer_nature_skipped_credentials",),
    },
    "elsevier": {
        "result": ("elsevier_fetched",),
        "error": ("elsevier_errors",),
        "skipped": ("elsevier_skipped_credentials",),
    },
    "biorxiv-api": {
        "result": ("preprint_fetched",),
        "error": ("preprint_source_errors",),
        "skipped": (),
    },
    "who-iris-oai": {
        "result": ("official_guidance_fetched", "official_guidance_records_seen"),
        "error": ("official_guidance_errors",),
        "skipped": (),
    },
    "controlled-query": {
        "result": ("controlled_discovery_fetched", "controlled_discovery_queries"),
        "error": ("controlled_discovery_query_errors",),
        "skipped": (),
    },
}


@dataclass(frozen=True)
class HealthThresholds:
    """SLO and alert limits.  Every value can be overridden by the CLI JSON file."""

    max_sync_age_hours: float = 12.0
    max_source_lag_hours: float = 24.0
    max_consecutive_failures: int = 0
    max_stale_run_minutes: float = 120.0
    max_backfill_stalled_hours: float = 24.0
    min_classification_current_ratio: float = 0.99
    min_openalex_coverage: float = 0.90
    min_unpaywall_coverage: float = 0.90
    min_bilingual_public_ratio: float = 1.0
    min_public_articles: int = 1
    max_release_age_hours: float = 24.0
    max_release_blockers: int = 0
    max_digest_age_days: float = 10.0
    task_history_hours: float = 24.0
    max_stale_task_minutes: float = 180.0
    max_latest_failed_task_types: int = 0
    max_exception_backlog: int = 500
    max_evidence_gap_errors: int = 0
    run_history_limit: int = 50
    task_history_limit: int = 500

    def __post_init__(self) -> None:
        nonnegative = {
            "max_sync_age_hours",
            "max_source_lag_hours",
            "max_consecutive_failures",
            "max_stale_run_minutes",
            "max_backfill_stalled_hours",
            "min_public_articles",
            "max_release_age_hours",
            "max_release_blockers",
            "max_digest_age_days",
            "task_history_hours",
            "max_stale_task_minutes",
            "max_latest_failed_task_types",
            "max_exception_backlog",
            "max_evidence_gap_errors",
        }
        positive = {"run_history_limit", "task_history_limit"}
        ratios = {
            "min_classification_current_ratio",
            "min_openalex_coverage",
            "min_unpaywall_coverage",
            "min_bilingual_public_ratio",
        }
        values = asdict(self)
        if any(values[name] < 0 for name in nonnegative):
            raise ValueError("health thresholds must be non-negative")
        if any(values[name] < 1 for name in positive):
            raise ValueError("health history limits must be positive")
        if any(not 0.0 <= float(values[name]) <= 1.0 for name in ratios):
            raise ValueError("health ratio thresholds must be between zero and one")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HealthThresholds":
        if not isinstance(value, Mapping):
            raise ValueError("health thresholds must be a JSON object")
        allowed = {field.name for field in fields(cls)}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown health threshold keys: " + ", ".join(unknown))
        return cls(**dict(value))


@dataclass(frozen=True)
class ResearchRadarSnapshot:
    """Internal inputs.  These rows are reduced before report serialization."""

    collected_at: datetime
    ingest_runs: tuple[Mapping[str, Any], ...]
    tasks: tuple[Mapping[str, Any], ...]
    articles: tuple[Mapping[str, Any], ...]
    summaries: tuple[Mapping[str, Any], ...]
    evidence_gaps: tuple[Mapping[str, Any], ...]
    release_payload: Mapping[str, Any] | None
    release_read_status: str
    backfill_checkpoint: Mapping[str, Any] | None
    backfill_read_status: str
    expected_sources: tuple[str, ...]
    current_review_link_count: int = 0
    # Database collectors populate compact aggregate counters so health checks
    # never materialize every article's large provider payload. Hand-built
    # snapshots may leave this unset and retain the row-based evaluation path.
    article_metrics: Mapping[str, int] | None = None


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_hours(now: datetime, value: Any) -> float | None:
    parsed = _utc(value)
    if parsed is None:
        return None
    return round(max(0.0, (now - parsed).total_seconds() / 3600.0), 3)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _nonnegative_count(value: Any) -> int | None:
    """Parse a persisted counter without turning malformed values into zero."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _source_run_outcome(
    source: str,
    *,
    completed_sources: set[str],
    counts: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce one provider's latest-run counters to a privacy-safe verdict."""

    if source not in completed_sources:
        return {
            "source": source,
            "status": "failed",
            "reason": "not_in_latest_completed_run",
            "error_count": 0,
            "skipped_credentials_count": 0,
        }
    contract = _SOURCE_RESULT_COUNT_CONTRACTS.get(source)
    if contract is None:
        return {
            "source": source,
            "status": "failed",
            "reason": "unknown_count_contract",
            "error_count": 0,
            "skipped_credentials_count": 0,
        }
    required_keys = (
        *contract["result"],
        *contract["error"],
        *contract["skipped"],
        *contract.get("attempt_any", ()),
    )
    if any(key not in counts for key in required_keys):
        return {
            "source": source,
            "status": "failed",
            "reason": "missing_count_contract",
            "error_count": 0,
            "skipped_credentials_count": 0,
        }
    parsed = {key: _nonnegative_count(counts.get(key)) for key in required_keys}
    if any(value is None for value in parsed.values()):
        return {
            "source": source,
            "status": "failed",
            "reason": "invalid_count_contract",
            "error_count": 0,
            "skipped_credentials_count": 0,
        }
    error_count = sum(parsed[key] or 0 for key in contract["error"])
    skipped_count = sum(parsed[key] or 0 for key in contract["skipped"])
    if skipped_count:
        reason = "skipped_credentials"
    elif error_count:
        reason = "provider_errors"
    elif contract.get("attempt_any") and not any(
        parsed[key] for key in contract["attempt_any"]
    ):
        reason = "not_attempted"
    else:
        reason = "success"
    return {
        "source": source,
        "status": "success" if reason == "success" else "failed",
        "reason": reason,
        "error_count": error_count,
        "skipped_credentials_count": skipped_count,
    }


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _read_json_object(path: Path, maximum_bytes: int) -> tuple[dict[str, Any] | None, str]:
    try:
        stat = path.stat()
        if stat.st_size > maximum_bytes:
            return None, "too_large"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "invalid_shape"
    return payload, "ok"


def expected_source_names(settings: Any | None) -> tuple[str, ...]:
    """Return public source labels without serializing configuration or secrets."""

    sources = ["crossref"]
    toggles = (
        ("europe_pmc_enabled", "europe-pmc"),
        ("openalex_enabled", "openalex"),
        ("unpaywall_enabled", "unpaywall"),
        ("publisher_rss_enabled", "publisher-rss"),
        ("springer_nature_enabled", "springer-nature"),
        ("elsevier_enabled", "elsevier"),
        ("preprint_discovery_enabled", "biorxiv-api"),
        ("official_guidance_enabled", "who-iris-oai"),
        ("controlled_discovery_enabled", "controlled-query"),
    )
    for attribute, label in toggles:
        if settings is not None and bool(getattr(settings, attribute, False)):
            sources.append(label)
    return tuple(sources)


async def collect_health_snapshot(
    db: Any,
    *,
    thresholds: HealthThresholds | None = None,
    release_path: Path | str = DEFAULT_RELEASE_PATH,
    backfill_checkpoint_path: Path | str = DEFAULT_CHECKPOINT_PATH,
    settings: Any | None = None,
    now: datetime | None = None,
) -> ResearchRadarSnapshot:
    """Collect a bounded snapshot using SELECTs only."""

    limits = thresholds or HealthThresholds()
    collected_at = _utc(now) or datetime.now(timezone.utc)
    runs = list((await db.execute(
        select(LiteratureIngestRun)
        .order_by(LiteratureIngestRun.started_at.desc(), LiteratureIngestRun.id.desc())
        .limit(limits.run_history_limit)
    )).scalars().all())
    task_cutoff = collected_at - timedelta(hours=limits.task_history_hours)
    tasks = list((await db.execute(
        select(Task)
        .where(Task.task_type.in_((
            TaskType.SYNC_LITERATURE,
            TaskType.ENRICH_LITERATURE,
            TaskType.DISCOVER_LITERATURE_GAPS,
        )))
        .where(Task.created_at >= task_cutoff)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limits.task_history_limit)
    )).scalars().all())
    doi_present = and_(
        LiteratureArticle.doi.is_not(None),
        func.length(func.trim(LiteratureArticle.doi)) > 0,
    )
    article_metrics_row = (await db.execute(select(
        func.count(LiteratureArticle.id).label("article_count"),
        func.sum(case((
            LiteratureArticle.metadata_["classification_version"].as_integer()
            >= CLASSIFICATION_VERSION,
            1,
        ), else_=0)).label("classification_current_count"),
        func.sum(case((doi_present, 1), else_=0)).label("doi_article_count"),
        func.sum(case((and_(
            doi_present,
            LiteratureArticle.openalex_id.is_not(None),
        ), 1), else_=0)).label("openalex_count"),
        func.sum(case((and_(
            doi_present,
            # ``as_string`` compiles to PostgreSQL ->> / SQLite JSON_EXTRACT,
            # preserving SQL NULL for a missing key without loading the object.
            LiteratureArticle.source_payload["unpaywall"].as_string().is_not(None),
        ), 1), else_=0)).label("unpaywall_count"),
    ))).mappings().one()
    # Operations only needs the small editorial decision subset. Selecting all
    # provider payloads here previously produced multi-gigabyte ORM allocations.
    articles = list((await db.execute(select(
        LiteratureArticle.metadata_,
        LiteratureArticle.publication_status,
    ).where(LiteratureArticle.publication_status == "review"))).mappings().all())
    summaries = list((await db.execute(select(
        LiteratureSummary.status,
        LiteratureSummary.generation_metadata,
    ).where(LiteratureSummary.status.in_(("review", "archived"))))).mappings().all())
    current_review_link_count = _safe_int((await db.execute(
        select(func.count())
        .select_from(LiteratureSignalArticleLink)
        .where(LiteratureSignalArticleLink.status == "review")
    )).scalar_one())
    gaps = list((await db.execute(select(
        LiteratureEvidenceGap.status,
        LiteratureEvidenceGap.error,
    ))).mappings().all())

    release, release_status = _read_json_object(Path(release_path), _MAX_RELEASE_BYTES)
    backfill, backfill_status = _read_json_object(
        Path(backfill_checkpoint_path), _MAX_CHECKPOINT_BYTES
    )
    return ResearchRadarSnapshot(
        collected_at=collected_at,
        ingest_runs=tuple({
            "source": run.source,
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "through_indexed_at": run.through_indexed_at,
            "checkpoint": dict(run.checkpoint or {}),
            "counts": dict(run.counts or {}),
        } for run in runs),
        tasks=tuple({
            "type": _enum_value(task.task_type),
            "status": _enum_value(task.status),
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "updated_at": task.updated_at,
            "retry_count": _safe_int(task.retry_count),
            "enrichment": {
                key: ((task.output_data or {}).get("summaries") or {}).get(key)
                for key in ("articles", "generated", "skipped", "failed")
                if (
                    _enum_value(task.task_type) == TaskType.ENRICH_LITERATURE.value
                    and isinstance(task.output_data, Mapping)
                    and isinstance(task.output_data.get("summaries"), Mapping)
                    and key in task.output_data["summaries"]
                )
            },
            "catch_up": {
                key: (task.output_data or {}).get(key)
                for key in (
                    "catch_up_required",
                    "catch_up_status",
                    "catch_up_next_action_code",
                    "catch_up_next_run_at",
                    "catch_up_backlog_observed_count",
                    "catch_up_backlog_projected_upper_bound",
                    "catch_up_backlog_limit",
                    "catch_up_resume_below_backlog",
                    "catch_up_required_backlog_reduction",
                    "catch_up_backpressure_reason",
                )
                if isinstance(task.output_data, Mapping) and key in task.output_data
            },
        } for task in tasks),
        articles=tuple(dict(row) for row in articles),
        summaries=tuple(dict(row) for row in summaries),
        current_review_link_count=current_review_link_count,
        evidence_gaps=tuple(dict(row) for row in gaps),
        release_payload=release,
        release_read_status=release_status,
        backfill_checkpoint=backfill,
        backfill_read_status=backfill_status,
        expected_sources=expected_source_names(settings),
        article_metrics={
            key: _safe_int(article_metrics_row.get(key))
            for key in (
                "article_count",
                "classification_current_count",
                "doi_article_count",
                "openalex_count",
                "unpaywall_count",
            )
        },
    )


def _check(
    code: str,
    status: str,
    observed: Mapping[str, Any],
    threshold: Mapping[str, Any] | None = None,
    *,
    next_action_code: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "next_action_code": next_action_code or (
            "none" if status == "pass" else f"inspect_{code}"
        ),
        "observed": dict(observed),
        "threshold": dict(threshold or {}),
    }


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


def _article_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    metrics = snapshot.article_metrics
    total = (
        _safe_int(metrics.get("article_count"))
        if isinstance(metrics, Mapping)
        else len(snapshot.articles)
    )
    current = (
        _safe_int(metrics.get("classification_current_count"))
        if isinstance(metrics, Mapping)
        else sum(
            _safe_int((row.get("metadata_") or {}).get("classification_version"))
            >= CLASSIFICATION_VERSION
            for row in snapshot.articles
        )
    )
    current_ratio = current / total if total else 0.0
    if isinstance(metrics, Mapping):
        denominator = _safe_int(metrics.get("doi_article_count"))
        openalex = _safe_int(metrics.get("openalex_count"))
        unpaywall = _safe_int(metrics.get("unpaywall_count"))
    else:
        doi_rows = [row for row in snapshot.articles if str(row.get("doi") or "").strip()]
        openalex = sum(bool(row.get("openalex_id")) for row in doi_rows)
        unpaywall = sum(
            isinstance(row.get("source_payload"), Mapping)
            and isinstance(row.get("source_payload", {}).get("unpaywall"), Mapping)
            for row in doi_rows
        )
        denominator = len(doi_rows)
    openalex_ratio = openalex / denominator if denominator else 0.0
    unpaywall_ratio = unpaywall / denominator if denominator else 0.0
    return [
        _check(
            "classification_version",
            "pass" if total and current_ratio >= thresholds.min_classification_current_ratio else "critical",
            {
                "article_count": total,
                "current_count": current,
                "stale_count": total - current,
                "current_ratio": round(current_ratio, 6),
                "required_version": CLASSIFICATION_VERSION,
            },
            {"min_current_ratio": thresholds.min_classification_current_ratio},
        ),
        _check(
            "metadata_provider_coverage",
            "pass" if (
                denominator
                and openalex_ratio >= thresholds.min_openalex_coverage
                and unpaywall_ratio >= thresholds.min_unpaywall_coverage
            ) else "critical",
            {
                "doi_article_count": denominator,
                "openalex_count": openalex,
                "openalex_ratio": round(openalex_ratio, 6),
                "unpaywall_count": unpaywall,
                "unpaywall_ratio": round(unpaywall_ratio, 6),
            },
            {
                "min_openalex_coverage": thresholds.min_openalex_coverage,
                "min_unpaywall_coverage": thresholds.min_unpaywall_coverage,
            },
            next_action_code=(
                "none"
                if denominator
                and openalex_ratio >= thresholds.min_openalex_coverage
                and unpaywall_ratio >= thresholds.min_unpaywall_coverage
                else "run_metadata_backfill_dry_run"
            ),
        ),
    ]


def _blocker_categories(blockers: Sequence[str]) -> dict[str, int]:
    categories: Counter[str] = Counter()
    patterns = (
        ("bilingual", "bilingual_gate"),
        ("classification", "classification"),
        ("private fields", "private_data"),
        ("integrity", "integrity"),
        ("duplicate", "duplicate"),
        ("peer-reviewed", "peer_review"),
        ("preprint", "peer_review"),
        ("signal", "surveillance_evidence"),
        ("weekly brief", "weekly_brief"),
        ("editorial", "editorial_gate"),
        ("indexable", "editorial_gate"),
        ("research domain", "classification"),
    )
    for blocker in blockers:
        lowered = blocker.lower()
        category = next((label for needle, label in patterns if needle in lowered), "other")
        categories[category] += 1
    return dict(sorted(categories.items()))


def _release_checks(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds,
) -> list[dict[str, Any]]:
    payload = snapshot.release_payload
    if not isinstance(payload, Mapping):
        missing = {"read_status": snapshot.release_read_status, "article_count": 0}
        return [
            _check("public_bilingual_gate", "critical", missing),
            _check("release_validator", "critical", {**missing, "blocker_count": None}),
            _check("release_freshness", "critical", {**missing, "age_hours": None}),
            _check("weekly_digest", "critical", {"read_status": snapshot.release_read_status}),
        ]
    articles = [item for item in payload.get("articles") or [] if isinstance(item, Mapping)]
    preprints = [item for item in payload.get("preprints") or [] if isinstance(item, Mapping)]
    public = [*articles, *preprints]
    bilingual = sum(
        bool((item.get("summary") or {}).get("en"))
        and bool((item.get("summary") or {}).get("zh"))
        for item in public
    )
    bilingual_ratio = bilingual / len(public) if public else 0.0
    bilingual_ok = (
        len(public) >= thresholds.min_public_articles
        and bilingual_ratio >= thresholds.min_bilingual_public_ratio
    )
    blockers = validate_public_research_payload(dict(payload))
    release_age = _age_hours(snapshot.collected_at, payload.get("last_updated"))
    release_fresh = release_age is not None and release_age <= thresholds.max_release_age_hours

    briefs = [item for item in payload.get("weekly_briefs") or [] if isinstance(item, Mapping)]
    latest_brief = briefs[0] if briefs else None
    brief_date: date | None = None
    if latest_brief and latest_brief.get("end_date"):
        try:
            brief_date = date.fromisoformat(str(latest_brief["end_date"]))
        except ValueError:
            pass
    digest_age = (
        max(0.0, (snapshot.collected_at.date() - brief_date).days)
        if brief_date is not None else None
    )
    brief_status = str((latest_brief or {}).get("brief_status") or "")
    reviewer = (
        ((latest_brief or {}).get("byline") or {}).get("reviewer")
        if isinstance((latest_brief or {}).get("byline"), Mapping)
        else None
    )
    ai_review = (
        ((latest_brief or {}).get("byline") or {}).get("ai_review")
        if isinstance((latest_brief or {}).get("byline"), Mapping)
        else None
    )
    review_evidence_valid = (
        brief_status == "automatically_compiled_not_editorially_reviewed"
        and reviewer is None
    ) or (
        brief_status == "editorially_reviewed"
        and project_weekly_editorial_review(reviewer, now=snapshot.collected_at) is not None
    ) or (
        brief_status == "ai_reviewed"
        and reviewer is None
        and project_weekly_ai_review(ai_review, now=snapshot.collected_at) is not None
    )
    digest_valid = bool(
        latest_brief
        and review_evidence_valid
        and digest_age is not None
        and digest_age <= thresholds.max_digest_age_days
    )
    return [
        _check(
            "public_bilingual_gate",
            "pass" if bilingual_ok else "critical",
            {
                "article_count": len(public),
                "bilingual_count": bilingual,
                "missing_bilingual_count": len(public) - bilingual,
                "bilingual_ratio": round(bilingual_ratio, 6),
            },
            {
                "min_public_articles": thresholds.min_public_articles,
                "min_bilingual_ratio": thresholds.min_bilingual_public_ratio,
            },
        ),
        _check(
            "release_validator",
            "pass" if len(blockers) <= thresholds.max_release_blockers else "critical",
            {
                "blocker_count": len(blockers),
                "blocker_categories": _blocker_categories(blockers),
                "integrity_alert_count": len(payload.get("integrity_alerts") or []),
                "preprint_count": len(preprints),
            },
            {"max_release_blockers": thresholds.max_release_blockers},
        ),
        _check(
            "release_freshness",
            "pass" if release_fresh else "critical",
            {"age_hours": release_age, "read_status": snapshot.release_read_status},
            {"max_release_age_hours": thresholds.max_release_age_hours},
        ),
        _check(
            "weekly_digest",
            "pass" if digest_valid else "critical",
            {
                "brief_present": latest_brief is not None,
                "brief_age_days": digest_age,
                "cited_finding_count": len((latest_brief or {}).get("cited_findings") or []),
                "brief_status": brief_status or "missing",
                "human_reviewed": brief_status == "editorially_reviewed",
                "ai_reviewed": brief_status == "ai_reviewed",
                "review_evidence_valid": review_evidence_valid,
            },
            {"max_digest_age_days": thresholds.max_digest_age_days},
        ),
    ]


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


def evaluate_health(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate a snapshot into a deterministic, safe-to-publish JSON object."""

    limits = thresholds or HealthThresholds()
    checks = [
        *_pipeline_checks(snapshot, limits),
        _backfill_check(snapshot, limits),
        *_article_checks(snapshot, limits),
        *_release_checks(snapshot, limits),
        *_operations_checks(snapshot, limits),
    ]
    counts = Counter(check["status"] for check in checks)
    overall = "unhealthy" if counts["critical"] else (
        "degraded" if counts["warning"] else "healthy"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "service": "research-radar",
        "status": overall,
        "generated_at": snapshot.collected_at.isoformat(),
        "summary": {
            "check_count": len(checks),
            "passed": counts["pass"],
            "warnings": counts["warning"],
            "critical": counts["critical"],
        },
        "checks": checks,
    }


def exit_code_for(report: Mapping[str, Any], *, fail_on: str = "warning") -> int:
    """Return 0/1/2 for healthy/degraded/unhealthy; 3 is reserved for CLI errors."""

    status = str(report.get("status") or "unhealthy")
    if status == "unhealthy":
        return 2
    if status == "degraded" and fail_on == "warning":
        return 1
    return 0


__all__ = [
    "DEFAULT_RELEASE_PATH",
    "HealthThresholds",
    "REPORT_SCHEMA_VERSION",
    "ResearchRadarSnapshot",
    "collect_health_snapshot",
    "evaluate_health",
    "exit_code_for",
    "expected_source_names",
]
