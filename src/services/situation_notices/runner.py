"""Situation runs: a scheduled refresh that writes the database, and a release export.

:func:`run_situation` collects reports, stores them, fetches full text and runs
peer-reviewed alert extraction (the scheduler calls it with ``export=False``).
:func:`export_situation` is the release step: it only reads the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from src.core import get_logger
from src.services.global_sources.collection import DEFAULT_SINCE_DAYS, collect

from .export import DEFAULT_OUTPUT_DIR, export_site
from .pipeline import (
    extract_pending,
    fetch_pending_documents,
    rebuild_outbreaks,
    requeue_stale_prompts,
    store_notices,
    utc_now,
)

logger = get_logger(__name__)

Progress = Callable[[int], Awaitable[None]]


async def _noop(_: int) -> None:
    return None


async def run_situation(
    *,
    since_days: int = DEFAULT_SINCE_DAYS,
    don_limit: int = 200,
    document_limit: int = 40,
    ai_limit: int = 30,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    skip_ai: bool = False,
    require_cross_model: bool = False,
    fetch: bool = True,
    export: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    now = utc_now()
    run_id = f"situation-{now.strftime('%Y%m%dT%H%M%SZ')}"
    summary: dict[str, Any] = {"run_id": run_id, "started_at": now.isoformat()}

    if fetch:
        # ``collect`` is synchronous HTTP; it isolates each source internally.
        envelope = collect(since_days=since_days, don_limit=don_limit)
        summary["sources"] = envelope["sources"]
        summary["stored"] = await store_notices(envelope, run_id=run_id)
    await progress(20)

    summary["documents"] = await fetch_pending_documents(limit=document_limit)
    await progress(40)

    summary["review_mode"] = None
    if not skip_ai:
        from src.ai.peer_review import AIOutputError
        from src.ai.peer_review_completer import ReviewRoutePool

        try:
            # A pool picks each report's author/reviewer by load and keeps failover
            # off the other role's model, so a rate-limited reviewer cannot collapse
            # both roles onto one model.
            pool = await ReviewRoutePool.from_model_center(line="situation_notice")
            routes = len(pool._capacity)
            if require_cross_model and routes < 2:
                raise AIOutputError(f"peer review needs at least two active model routes; found {routes}")
        except Exception as exc:  # noqa: BLE001 - no model must not block the export
            logger.warning("Situation AI unavailable, exporting what is already extracted: {}", exc)
            summary["ai_error"] = f"{type(exc).__name__}: {exc}"
        else:
            summary["review_mode"] = "cross_model" if routes >= 2 else "single_model"
            summary["requeued_stale_prompts"] = await requeue_stale_prompts()
            summary["extraction"] = await extract_pending(pool, limit=ai_limit)
    await progress(75)

    summary["outbreaks"] = await rebuild_outbreaks(as_of=now.date())
    if export:
        summary["export"] = await export_site(output_dir=output_dir, as_of=now.date())
    summary["finished_at"] = utc_now().isoformat()
    await progress(100)
    return summary


async def export_situation(*, output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Release step: publish what the database already holds; no feed requests, no model calls."""

    now = utc_now()
    return {
        "started_at": now.isoformat(),
        "outbreaks": await rebuild_outbreaks(as_of=now.date()),
        "export": await export_site(output_dir=output_dir, as_of=now.date()),
        "finished_at": utc_now().isoformat(),
    }
