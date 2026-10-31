"""Situation runs: a scheduled refresh that writes the database, and a release export.

:func:`run_situation` collects reports, stores them, fetches full text and runs
peer-reviewed alert extraction and commits stored outbreaks.
:func:`export_situation` is the release step: it only reads the database.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4
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
    skip_ai: bool = False,
    require_cross_model: bool = False,
    fetch: bool = True,
    task_uuid: str | None = None,
    progress: Progress = _noop,
) -> dict[str, Any]:
    now = utc_now()
    run_id = f"situation-{now.strftime('%Y%m%dT%H%M%SZ')}"
    summary: dict[str, Any] = {"run_id": run_id, "started_at": now.isoformat()}

    from src.services.ingestion.stages import StageRun
    stages = await StageRun.start(task_uuid or str(uuid4()), "situation")
    await stages.transition("collect", metrics={"step": "documents"})
    from src.services.ingestion.capture import capture_sources
    try:
        with capture_sources(stages.task_uuid, stages.attempt, "situation"):
            return await _run_stages(summary=summary, now=now, run_id=run_id, stages=stages, since_days=since_days, don_limit=don_limit, document_limit=document_limit, ai_limit=ai_limit, skip_ai=skip_ai, require_cross_model=require_cross_model, fetch=fetch, progress=progress)
    except BaseException as exc:
        await stages.finish(error=str(exc) or type(exc).__name__)
        raise


async def _run_stages(*, summary, now, run_id, stages, since_days, don_limit, document_limit, ai_limit, skip_ai, require_cross_model, fetch, progress):
    if fetch:
        # ``collect`` is synchronous HTTP; it isolates each source internally.
        envelope = await asyncio.to_thread(collect, since_days=since_days, don_limit=don_limit)
        summary["sources"] = envelope["sources"]
        summary["stored"] = await store_notices(envelope, run_id=run_id)
    await progress(20)

    summary["documents"] = await fetch_pending_documents(limit=document_limit)
    await progress(40)

    from src.services.ingestion.capture import _current
    if _current.get():
        _current.get().verify()
    await stages.transition("organize", metrics={"step": "extract"})
    summary["review_mode"] = None
    if not skip_ai:
        from src.ai.peer_review_completer import QueuedModelCompleter
        complete = QueuedModelCompleter(line="situation_notice", allow_single_model=not require_cross_model)

        summary["review_mode"] = "scheduled"
        summary["requeued_stale_prompts"] = await requeue_stale_prompts()
        summary["extraction"] = await extract_pending(complete, limit=ai_limit)
    await stages.finish(metrics=summary.get("extraction") or {})
    await progress(75)

    await stages.transition("store")
    summary["outbreaks"] = await rebuild_outbreaks(as_of=now.date(), task_uuid=stages.task_uuid)
    await stages.finish(metrics={"outbreaks": summary["outbreaks"]})
    summary["finished_at"] = utc_now().isoformat()
    await progress(100)
    return summary


async def export_situation(*, output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Release step: publish what the database already holds; no feed requests, no model calls."""

    now = utc_now()
    return {
        "started_at": now.isoformat(),
        "export": await export_site(output_dir=output_dir, as_of=now.date()),
        "finished_at": utc_now().isoformat(),
    }
