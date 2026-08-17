"""Resumable, editorial-safe OpenAlex and Unpaywall backfill for stored articles."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from sqlalchemy import select

from src.core.config import get_config
from src.core.database import get_db
from src.domain import LiteratureArticle

from .clients import OpenAlexClient, UnpaywallClient
from .normalization import apply_openalex, apply_unpaywall, normalize_doi
from .reclassification import candidate_from_stored_article


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_PATH = ROOT / "data/cache/literature_metadata_backfill.json"
CHECKPOINT_VERSION = 1
SUPPORTED_PROVIDERS = ("openalex", "unpaywall")


def _load_checkpoint(path: Path, providers: tuple[str, ...]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != CHECKPOINT_VERSION:
        raise ValueError(f"Unsupported metadata-backfill checkpoint: {path}")
    if tuple(payload.get("providers") or []) != providers:
        raise ValueError("Checkpoint providers differ from this run; use --no-resume or a different checkpoint file")
    return payload


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
    last_database_id = int((previous or {}).get("last_database_id") or 0)
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
        "batch_size": batch_size,
        "limit": limit,
        "concurrency": request_concurrency,
        "min_interval_seconds": request_interval,
        "resumed_from_database_id": resumed_from_id,
        "last_database_id": last_database_id,
        "last_article_id": last_article_id,
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
        "started_at": started_at.isoformat(),
    }

    async with database() as db:
        while limit is None or stats["examined"] < limit:
            if stats["batches"] and request_interval:
                # Provider clients pace concurrent requests within a batch;
                # this also spaces the boundary between consecutive batches.
                await asyncio.sleep(request_interval)
            remaining = batch_size if limit is None else min(batch_size, limit - stats["examined"])
            query = (
                select(LiteratureArticle)
                .where(
                    LiteratureArticle.id > last_database_id,
                    LiteratureArticle.doi.is_not(None),
                )
                .order_by(LiteratureArticle.id)
                .limit(remaining)
            )
            articles = list((await db.execute(query)).scalars().all())
            if not articles:
                stats["status"] = "completed"
                break
            stats["batches"] += 1
            stats["examined"] += len(articles)
            dois = [doi for article in articles if (doi := normalize_doi(article.doi))]
            provider_results: dict[str, dict[str, dict[str, Any]]] = {}
            batch_failed = False

            for provider in normalized_providers:
                provider_stats = stats["provider_stats"][provider]
                provider_stats["requested"] += len(dois)
                provider_failed = False
                try:
                    if provider == "openalex":
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
                provider_stats["matched"] += len(results)
                if not provider_failed:
                    provider_stats["not_found"] += max(0, len(dois) - len(results))

            for article in articles:
                candidate = candidate_from_stored_article(article)
                before = _backfill_projection(candidate)
                doi = normalize_doi(article.doi) or ""
                if payload := provider_results.get("unpaywall", {}).get(doi):
                    apply_unpaywall(candidate, payload)
                if payload := provider_results.get("openalex", {}).get(doi):
                    apply_openalex(candidate, payload)
                changed = before != _backfill_projection(candidate)
                stats["updated"] += int(changed)
                stats["unchanged"] += int(not changed)
                if apply and changed:
                    _apply_candidate_projection(article, candidate)

            if apply:
                await db.commit()
            if batch_failed:
                stats["status"] = "stopped_on_provider_error"
                break

            last_database_id = int(articles[-1].id)
            last_article_id = articles[-1].article_id
            stats["last_database_id"] = last_database_id
            stats["last_article_id"] = last_article_id
            if apply:
                checkpoint = {
                    "version": CHECKPOINT_VERSION,
                    "providers": list(normalized_providers),
                    "last_database_id": last_database_id,
                    "last_article_id": last_article_id,
                    "status": "running",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": stats["run_id"],
                    "run_stats": {
                        key: stats[key]
                        for key in ("examined", "batches", "updated", "unchanged", "provider_stats")
                    },
                }
                _write_checkpoint(checkpoint_file, checkpoint)
                stats["checkpoint_written"] = True
        else:
            stats["status"] = "completed_at_limit"

    if stats["status"] == "running":
        stats["status"] = "completed_at_limit" if limit is not None else "completed"
    stats["failures"] = stats["failures"][:100]
    stats["completed_at"] = datetime.now(timezone.utc).isoformat()
    if apply:
        checkpoint = {
            "version": CHECKPOINT_VERSION,
            "providers": list(normalized_providers),
            "last_database_id": stats["last_database_id"],
            "last_article_id": stats["last_article_id"],
            "status": stats["status"],
            "updated_at": stats["completed_at"],
            "run_id": stats["run_id"],
            "run_stats": {
                key: stats[key]
                for key in (
                    "examined", "batches", "updated", "unchanged",
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
