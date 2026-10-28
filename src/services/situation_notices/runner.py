"""One end-to-end Situation run, shared by the CLI, the task worker and releases.

collect authority notices -> store -> peer-reviewed AI enrichment -> events ->
weekly digests -> static export.  ``skip_ai`` keeps the run usable when no
model route is available: notices still publish with their source metadata.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.core import get_logger
from src.services.global_sources.catalogue import iso_week
from src.services.global_sources.collection import DEFAULT_SINCE_DAYS, collect
from src.services.global_sources.export_static import DEFAULT_OUTPUT_DIR, DEFAULT_RECENT_LIMIT

from .export import export_site
from .pipeline import (
    enrich_pending,
    rebuild_events,
    requeue_stale_prompts,
    store_notices,
    utc_now,
    write_weekly_digest,
)

logger = get_logger(__name__)

Progress = Callable[[int], Awaitable[None]]


async def _noop(_: int) -> None:
    return None


async def run_situation(
    *,
    since_days: int = DEFAULT_SINCE_DAYS,
    don_limit: int = 200,
    ai_limit: int = 50,
    digest_weeks: int = 2,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    skip_ai: bool = False,
    require_cross_model: bool = False,
    fetch: bool = True,
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
    await progress(30)

    if skip_ai:
        summary["review_mode"] = None
    else:
        from .models import ModelCenterCompleter

        try:
            completer = await ModelCenterCompleter.from_model_center(allow_single_model=not require_cross_model)
        except Exception as exc:  # noqa: BLE001 - no model must not block publication of metadata
            logger.warning("Situation AI unavailable, exporting metadata only: {}", exc)
            summary["ai_error"] = f"{type(exc).__name__}: {exc}"
            completer = None
        if completer is not None:
            summary["review_mode"] = "single_model" if completer.single_model else "cross_model"
            summary["requeued_stale_prompts"] = await requeue_stale_prompts()
            summary["enrichment"] = await enrich_pending(completer, limit=ai_limit)
            await progress(65)
            summary["events"] = await rebuild_events(as_of=now.date())
            digests: dict[str, str] = {}
            for offset in range(max(0, digest_weeks)):
                period = iso_week(now.date() - timedelta(weeks=offset))["iso_week_key"]
                digests[period] = await write_weekly_digest(completer, period)
            summary["digests"] = digests
    if "events" not in summary:
        summary["events"] = await rebuild_events(as_of=now.date())
    await progress(85)

    summary["export"] = await export_site(
        output_dir=output_dir,
        since_days=since_days,
        recent_limit=recent_limit,
        as_of=now.date(),
    )
    summary["finished_at"] = utc_now().isoformat()
    await progress(100)
    return summary
