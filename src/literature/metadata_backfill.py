"""Resumable, editorial-safe OpenAlex and Unpaywall backfill for stored articles."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from sqlalchemy import and_, case, func, or_, select

from src.core.config import get_config
from src.core.database import get_db
from src.domain import LiteratureArticle

from .clients import OpenAlexClient, UnpaywallClient
from .normalization import apply_openalex, apply_unpaywall, normalize_doi
from .reclassification import candidate_from_stored_article


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_PATH = ROOT / "data/cache/literature_metadata_backfill.json"
CHECKPOINT_VERSION = 2
SUPPORTED_PROVIDERS = ("openalex", "unpaywall")
DEFAULT_COVERAGE_TARGET = 0.95


def _load_checkpoint(path: Path, providers: tuple[str, ...]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") not in {1, CHECKPOINT_VERSION}:
        raise ValueError(f"Unsupported metadata-backfill checkpoint: {path}")
    if tuple(payload.get("providers") or []) != providers:
        raise ValueError("Checkpoint providers differ from this run; use --no-resume or a different checkpoint file")
    return payload


def _provider_present(provider: str, value: Any) -> bool:
    if provider == "openalex":
        return bool(getattr(value, "openalex_id", None))
    payload = getattr(value, "source_payload", None)
    return isinstance(payload, Mapping) and isinstance(payload.get("unpaywall"), Mapping)


def _provider_missing_predicate(provider: str) -> Any:
    """Return the SQL equivalent of ``not _provider_present``.

    Keeping this predicate aligned with the in-memory presence check lets a
    bounded invocation spend its limit on actual provider candidates instead
    of repeatedly scanning rows that already have metadata.
    """

    if provider == "openalex":
        return or_(
            LiteratureArticle.openalex_id.is_(None),
            LiteratureArticle.openalex_id == "",
        )
    return LiteratureArticle.source_payload["unpaywall"].as_string().is_(None)


def _coverage_plan(
    rows: list[Any],
    *,
    providers: tuple[str, ...],
    targets: Mapping[str, float],
) -> dict[str, dict[str, Any]]:
    """Build a deterministic provider plan from DOI-bearing rows."""

    denominator = len(rows)
    plan: dict[str, dict[str, Any]] = {}
    for provider in providers:
        covered = sum(_provider_present(provider, row) for row in rows)
        target_count = math.ceil(float(targets[provider]) * denominator)
        plan[provider] = {
            "eligible": denominator,
            "covered": covered,
            "missing": denominator - covered,
            "target": float(targets[provider]),
            "target_count": target_count,
            "deficit": max(0, target_count - covered),
            "ratio": round(covered / denominator, 6) if denominator else 1.0,
        }
    return plan


def _normalize_targets(
    providers: tuple[str, ...],
    targets: Mapping[str, float] | None,
    settings: Any,
) -> dict[str, float]:
    configured = {
        "openalex": getattr(settings, "metadata_backfill_openalex_target", DEFAULT_COVERAGE_TARGET),
        "unpaywall": getattr(settings, "metadata_backfill_unpaywall_target", DEFAULT_COVERAGE_TARGET),
    }
    if targets:
        unknown = sorted(set(targets) - set(SUPPORTED_PROVIDERS))
        if unknown:
            raise ValueError("unknown coverage target providers: " + ", ".join(unknown))
        configured.update(targets)
    normalized = {provider: float(configured[provider]) for provider in providers}
    if any(not 0.0 <= target <= 1.0 for target in normalized.values()):
        raise ValueError("coverage targets must be between zero and one")
    return normalized


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _backfill_projection(value: Any) -> dict[str, Any]:
    return {
        "openalex_id": value.openalex_id,
        "source_urls": dict(value.source_urls or {}),
        "open_access_status": value.open_access_status,
        "open_access_url": value.open_access_url,
        "license_url": value.license_url,
        "source_payload": dict(value.source_payload or {}),
    }


def _apply_candidate_projection(article: LiteratureArticle, candidate: Any) -> None:
    article.openalex_id = candidate.openalex_id or article.openalex_id
    article.source_urls = dict(candidate.source_urls or {})
    article.open_access_status = candidate.open_access_status
    article.open_access_url = candidate.open_access_url
    article.license_url = candidate.license_url
    article.source_payload = dict(candidate.source_payload or {})


async def backfill_existing_literature_metadata(
    *,
    apply: bool = False,
    batch_size: int = 50,
    limit: int | None = None,
    providers: tuple[str, ...] = SUPPORTED_PROVIDERS,
    checkpoint_path: Path | str | None = None,
    resume: bool = True,
    concurrency: int | None = None,
    min_interval_seconds: float | None = None,
    coverage_targets: Mapping[str, float] | None = None,
    config: Any | None = None,
    db_factory: Callable[[], Any] | None = None,
    openalex_client: Any | None = None,
    unpaywall_client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch bounded metadata batches while preserving all editorial state.

    Dry-run is the default and never writes the database or checkpoint. Apply
    mode commits one successful batch at a time and advances the file checkpoint
    only after that commit. A provider error stops the watermark before the
    affected batch so the next invocation retries it idempotently.
    """

    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch_size must be between 1 and 500")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    requested_providers = {str(value).strip().lower() for value in providers if value}
    if not requested_providers or any(value not in SUPPORTED_PROVIDERS for value in requested_providers):
        raise ValueError(f"providers must be selected from {', '.join(SUPPORTED_PROVIDERS)}")
    normalized_providers = tuple(value for value in SUPPORTED_PROVIDERS if value in requested_providers)

    settings = config or get_config().literature
    database = db_factory or get_db
    checkpoint_file = Path(checkpoint_path) if checkpoint_path else DEFAULT_CHECKPOINT_PATH
    started_at = now or datetime.now(timezone.utc)
    previous = _load_checkpoint(checkpoint_file, normalized_providers) if apply and resume else None
    legacy_cursor = int((previous or {}).get("last_database_id") or 0)
    stored_cursors = (previous or {}).get("provider_cursors") or {}
    provider_cursors = {
        provider: int(stored_cursors.get(provider, legacy_cursor) or 0)
        for provider in normalized_providers
    }
    last_database_id = min(provider_cursors.values(), default=legacy_cursor)
    last_article_id = (previous or {}).get("last_article_id")
    resumed_from_id = last_database_id
    request_concurrency = concurrency or settings.metadata_enrichment_concurrency
    request_interval = (
        settings.metadata_enrichment_min_interval_seconds
        if min_interval_seconds is None
        else min_interval_seconds
    )
    if request_concurrency < 1 or request_concurrency > 12:
        raise ValueError("concurrency must be between 1 and 12")
    if request_interval < 0 or request_interval > 10:
        raise ValueError("min_interval_seconds must be between 0 and 10")
    targets = _normalize_targets(normalized_providers, coverage_targets, settings)

    if "openalex" in normalized_providers and openalex_client is None:
        openalex_client = OpenAlexClient(
            mailto=settings.contact_email,
            api_key=settings.openalex_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            retries=settings.max_retries,
        )
    if "unpaywall" in normalized_providers and unpaywall_client is None:
        unpaywall_client = UnpaywallClient(
            email=settings.contact_email,
            timeout_seconds=settings.request_timeout_seconds,
            retries=settings.max_retries,
        )

    stats: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "mode": "apply" if apply else "dry_run",
        "status": "running",
        "providers": list(normalized_providers),
        "coverage_targets": targets,
        "batch_size": batch_size,
        "planned_batch_size_min": None,
        "planned_batch_size_max": None,
        "limit": limit,
        "concurrency": request_concurrency,
        "min_interval_seconds": request_interval,
        "resumed_from_database_id": resumed_from_id,
        "last_database_id": last_database_id,
        "last_article_id": last_article_id,
        "provider_cursors": dict(provider_cursors),
        "examined": 0,
        "batches": 0,
        "updated": 0,
        "unchanged": 0,
        "failure_count": 0,
        "failures": [],
        "provider_stats": {
            provider: {"requested": 0, "matched": 0, "not_found": 0, "failed": 0}
            for provider in normalized_providers
        },
        "checkpoint_file": str(checkpoint_file),
        "checkpoint_written": False,
        "preserved_editorial_state": True,
        "target_reached": False,
        "next_action_code": "continue_bounded_backfill",
        "started_at": started_at.isoformat(),
    }

    async with database() as db:
        # Coverage planning must remain an aggregate operation. Materializing
        # every DOI-bearing article also loads abstracts and provider JSON and
        # previously pushed a 500-record dry run above 9 GiB RSS.
        doi_present = LiteratureArticle.doi.is_not(None)
        coverage_row = (await db.execute(select(
            func.count(LiteratureArticle.id).label("eligible"),
            func.max(LiteratureArticle.id).label("maximum_database_id"),
            func.sum(case((and_(
                doi_present,
                LiteratureArticle.openalex_id.is_not(None),
                LiteratureArticle.openalex_id != "",
            ), 1), else_=0)).label("openalex_covered"),
            func.sum(case((and_(
                doi_present,
                LiteratureArticle.source_payload["unpaywall"].as_string().is_not(None),
            ), 1), else_=0)).label("unpaywall_covered"),
        ).where(doi_present))).mappings().one()
        eligible = int(coverage_row.get("eligible") or 0)
        coverage: dict[str, dict[str, Any]] = {}
        for provider in normalized_providers:
            covered = int(coverage_row.get(f"{provider}_covered") or 0)
            target_count = math.ceil(float(targets[provider]) * eligible)
            coverage[provider] = {
                "eligible": eligible,
                "covered": covered,
                "missing": eligible - covered,
                "target": float(targets[provider]),
                "target_count": target_count,
                "deficit": max(0, target_count - covered),
                "ratio": round(covered / eligible, 6) if eligible else 1.0,
            }
        stats["coverage_before"] = {
            provider: dict(values) for provider, values in coverage.items()
        }
        maximum_database_id = int(coverage_row.get("maximum_database_id") or 0)
        failed_providers: set[str] = set()
        while limit is None or stats["examined"] < limit:
            active_providers = [
                provider
                for provider in normalized_providers
                if coverage[provider]["deficit"] > 0
                and provider_cursors[provider] < maximum_database_id
                and provider not in failed_providers
            ]
            if not active_providers:
                stats["target_reached"] = all(
                    values["deficit"] == 0 for values in coverage.values()
                )
                stats["status"] = (
                    "completed"
                    if stats["target_reached"]
                    else "stopped_on_provider_error"
                    if failed_providers
                    else "completed_below_target"
                )
                stats["next_action_code"] = (
                    "none"
                    if stats["target_reached"]
                    else "retry_failed_provider_batch"
                    if failed_providers
                    else "review_provider_match_gap"
                )
                break
            if stats["batches"] and request_interval:
                # Provider clients pace concurrent requests within a batch;
                # this also spaces the boundary between consecutive batches.
                await asyncio.sleep(request_interval)
            largest_deficit = max(coverage[provider]["deficit"] for provider in active_providers)
            adaptive_batch_size = min(batch_size, max(1, largest_deficit))
            observed_batch_sizes = [
                value
                for value in (
                    stats["planned_batch_size_min"],
                    stats["planned_batch_size_max"],
                    adaptive_batch_size,
                )
                if value is not None
            ]
            stats["planned_batch_size_min"] = min(observed_batch_sizes)
            stats["planned_batch_size_max"] = max(observed_batch_sizes)
            remaining = (
                adaptive_batch_size
                if limit is None
                else min(adaptive_batch_size, limit - stats["examined"])
            )
            missing_after_cursor = [
                and_(
                    LiteratureArticle.id > provider_cursors[provider],
                    _provider_missing_predicate(provider),
                )
                for provider in active_providers
            ]
            query = (
                select(LiteratureArticle)
                .where(
                    LiteratureArticle.doi.is_not(None),
                    or_(*missing_after_cursor),
                )
                .order_by(LiteratureArticle.id)
                .limit(remaining)
            )
            articles = list((await db.execute(query)).scalars().all())
            if not articles:
                for provider in active_providers:
                    provider_cursors[provider] = maximum_database_id
                stats["status"] = "completed_below_target"
                stats["next_action_code"] = "review_provider_match_gap"
                break
            stats["batches"] += 1
            stats["examined"] += len(articles)
            provider_results: dict[str, dict[str, dict[str, Any]]] = {}
            provider_succeeded: dict[str, bool] = {}
            coverage_gains = {provider: 0 for provider in active_providers}
            batch_failed = False

            for provider in active_providers:
                dois = [
                    doi
                    for article in articles
                    if int(article.id) > provider_cursors[provider]
                    and not _provider_present(provider, article)
                    and (doi := normalize_doi(article.doi))
                ]
                provider_stats = stats["provider_stats"][provider]
                provider_stats["requested"] += len(dois)
                provider_failed = False
                try:
                    if not dois:
                        results = {}
                    elif provider == "openalex":
                        results = await openalex_client.enrich_by_dois(
                            dois,
                            batch_size=min(batch_size, settings.openalex_batch_size),
                            concurrency=request_concurrency,
                            min_interval_seconds=request_interval,
                        )
                    else:
                        results = await unpaywall_client.enrich_by_dois(
                            dois,
                            concurrency=request_concurrency,
                            min_interval_seconds=request_interval,
                        )
                except Exception as exc:
                    batch_failed = True
                    provider_failed = True
                    provider_stats["failed"] += len(dois)
                    stats["failure_count"] += len(dois)
                    stats["failures"].append({
                        "provider": provider,
                        "database_ids": [article.id for article in articles],
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    })
                    results = {}
                provider_results[provider] = results
                provider_succeeded[provider] = not provider_failed
                if provider_failed:
                    failed_providers.add(provider)
                provider_stats["matched"] += len(results)
                if not provider_failed:
                    provider_stats["not_found"] += max(0, len(dois) - len(results))

            for article in articles:
                candidate = candidate_from_stored_article(article)
                before = _backfill_projection(candidate)
                present_before = {
                    provider: _provider_present(provider, candidate)
                    for provider in active_providers
                }
                doi = normalize_doi(article.doi) or ""
                if payload := provider_results.get("unpaywall", {}).get(doi):
                    apply_unpaywall(candidate, payload)
                if payload := provider_results.get("openalex", {}).get(doi):
                    apply_openalex(candidate, payload)
                changed = before != _backfill_projection(candidate)
                for provider in active_providers:
                    coverage_gains[provider] += int(
                        not present_before[provider]
                        and _provider_present(provider, candidate)
                    )
                stats["updated"] += int(changed)
                stats["unchanged"] += int(not changed)
                if apply and changed:
                    _apply_candidate_projection(article, candidate)

            if apply:
                await db.commit()
            for provider in active_providers:
                if provider_succeeded.get(provider):
                    provider_cursors[provider] = max(
                        provider_cursors[provider], int(articles[-1].id)
                    )
                    coverage[provider]["covered"] += coverage_gains[provider]
                    coverage[provider]["covered"] = min(
                        coverage[provider]["eligible"], coverage[provider]["covered"]
                    )
                    coverage[provider]["missing"] = (
                        coverage[provider]["eligible"] - coverage[provider]["covered"]
                    )
                    coverage[provider]["deficit"] = max(
                        0,
                        coverage[provider]["target_count"] - coverage[provider]["covered"],
                    )
                    denominator = coverage[provider]["eligible"]
                    coverage[provider]["ratio"] = round(
                        coverage[provider]["covered"] / denominator, 6
                    ) if denominator else 1.0
            last_database_id = min(provider_cursors.values(), default=last_database_id)
            stats["last_database_id"] = last_database_id
            stats["provider_cursors"] = dict(provider_cursors)
            if batch_failed:
                stats["next_action_code"] = "retry_failed_provider_batch"

            last_article_id = articles[-1].article_id
            stats["last_article_id"] = last_article_id
            if apply:
                checkpoint = {
                    "version": CHECKPOINT_VERSION,
                    "providers": list(normalized_providers),
                    "last_database_id": last_database_id,
                    "last_article_id": last_article_id,
                    "provider_cursors": dict(provider_cursors),
                    "coverage_targets": targets,
                    "coverage": coverage,
                    "target_reached": all(
                        values["deficit"] == 0 for values in coverage.values()
                    ),
                    "status": "running",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": stats["run_id"],
                    "run_stats": {
                        key: stats[key]
                        for key in (
                            "examined", "batches", "updated", "unchanged",
                            "planned_batch_size_min", "planned_batch_size_max",
                            "provider_stats",
                        )
                    },
                }
                _write_checkpoint(checkpoint_file, checkpoint)
                stats["checkpoint_written"] = True
        else:
            stats["status"] = "completed_at_limit"

    if stats["status"] == "running":
        stats["status"] = (
            "stopped_on_provider_error"
            if failed_providers
            else "completed_at_limit"
            if limit is not None
            else "completed"
        )
    stats["coverage_after"] = {
        provider: dict(values) for provider, values in coverage.items()
    }
    stats["target_reached"] = all(values["deficit"] == 0 for values in coverage.values())
    if stats["target_reached"]:
        # Reaching the target on the record that also exhausts ``limit`` is a
        # completed run, not a misleading "completed_at_limit" continuation.
        if stats["status"] == "completed_at_limit":
            stats["status"] = "completed"
        stats["next_action_code"] = "none"
    elif stats["status"] == "completed_at_limit":
        stats["next_action_code"] = "continue_bounded_backfill"
    stats["failures"] = stats["failures"][:100]
    stats["completed_at"] = datetime.now(timezone.utc).isoformat()
    if apply:
        checkpoint = {
            "version": CHECKPOINT_VERSION,
            "providers": list(normalized_providers),
            "last_database_id": stats["last_database_id"],
            "last_article_id": stats["last_article_id"],
            "provider_cursors": dict(provider_cursors),
            "coverage_targets": targets,
            "coverage": stats["coverage_after"],
            "target_reached": stats["target_reached"],
            "next_action_code": stats["next_action_code"],
            "status": stats["status"],
            "updated_at": stats["completed_at"],
            "run_id": stats["run_id"],
            "run_stats": {
                key: stats[key]
                for key in (
                    "examined", "batches", "updated", "unchanged",
                    "planned_batch_size_min", "planned_batch_size_max",
                    "failure_count", "provider_stats",
                )
            },
            "failures": stats["failures"],
        }
        _write_checkpoint(checkpoint_file, checkpoint)
        stats["checkpoint_written"] = True
    return stats


__all__ = [
    "CHECKPOINT_VERSION",
    "DEFAULT_CHECKPOINT_PATH",
    "SUPPORTED_PROVIDERS",
    "backfill_existing_literature_metadata",
]
