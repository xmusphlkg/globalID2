"""Read-only, privacy-safe snapshot collection for Research Radar health checks.

The collector issues SELECT statements and reads generated JSON artifacts. It
reduces database state to bounded snapshot records for the evaluator.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
from .metadata_backfill import DEFAULT_CHECKPOINT_PATH

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
    def from_mapping(cls, value: Mapping[str, Any]) -> HealthThresholds:
        if not isinstance(value, Mapping):
            raise TypeError("health thresholds must be a JSON object")
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

__all__ = [
    "DEFAULT_RELEASE_PATH",
    "REPORT_SCHEMA_VERSION",
    "HealthThresholds",
    "ResearchRadarSnapshot",
    "collect_health_snapshot",
    "expected_source_names",
]
