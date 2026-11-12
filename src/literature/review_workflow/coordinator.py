"""Bounded durable review coordination; model capacity is owned by the runtime."""

import asyncio

from src.ai import runtime
from src.ai.model_center import get_runtime_routes
from src.services.exceptions import TaskCancelledError

from .repository import ADVANCE_BATCH_SIZE, PAGE_SIZE, ReviewWorkflowRepository

CAPACITY_REFRESH_SECONDS = 5
MAX_BUFFER_SIZE = 512
MIN_SUPPLY_TARGET = 64


def review_buffer_target(routes: list[dict], budget: int) -> int:
    """Keep two waves of eligible text work, bounded by the saved user budget."""
    permits = sum(
        max(0, int(route.get("runtime_model_capacity") or 0))
        for route in routes
        if route.get("available_for_routing")
        and "text" in (route.get("capabilities") or ["text"])
    )
    return min(budget, MAX_BUFFER_SIZE, max(MIN_SUPPLY_TARGET, 2 * permits))


async def run_literature_review(
    *,
    kind: str,
    buffer_size: int = MAX_BUFFER_SIZE,
    max_items: int | None = None,
    allow_degraded_review: bool = False,
    deadline_seconds: int = 21600,
) -> dict:
    """Resume a finite review pass from durable state under its queued parent.

    ``buffer_size`` is the maximum business-state budget. Supply follows twice
    the available text permits, with a small floor, inside that persisted budget.
    It does not reserve models or control provider concurrency. Waiting/deferred/skipped rows advance
    this pass's cursor and may be reconsidered by the next parent. Unchanged
    two-opinion holds stay parked until their content fingerprint changes.
    """
    if kind not in {"articles", "summaries"}:
        raise ValueError("kind must be articles or summaries")
    if type(buffer_size) is not int or not 1 <= buffer_size <= 512:
        raise ValueError("buffer_size must be between 1 and 512")
    if max_items is not None and (type(max_items) is not int or max_items < 1):
        raise ValueError("max_items must be positive")
    if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 86400:
        raise ValueError("deadline_seconds must be between 1 and 86400")
    if type(allow_degraded_review) is not bool:
        raise ValueError("allow_degraded_review must be an explicit boolean")
    parent = runtime.current_parent()
    if parent is None:
        raise RuntimeError("Literature review requires a queued parent task")
    from src.services.ai_content_governance_service import ai_content_governance_service

    repository = ReviewWorkflowRepository(
        ai_content_governance_service, generation=parent.generation
    )
    try:
        await repository.recover_orphans()
        settings = await repository.ensure_drain(
            parent.task_uuid,
            kind,
            {
                "buffer_size": buffer_size,
                "max_items": max_items,
                "allow_degraded_review": allow_degraded_review,
                "deadline_seconds": deadline_seconds,
            },
        )
        next_capacity_read = 0.0
        target = min(settings["buffer_size"], MIN_SUPPLY_TARGET)
        after_id = 0
        advances_since_feed = PAGE_SIZE
        while True:
            now = asyncio.get_running_loop().time()
            if now >= next_capacity_read:
                target = review_buffer_target(
                    await get_runtime_routes(), settings["buffer_size"]
                )
                next_capacity_read = now + CAPACITY_REFRESH_SECONDS
            fed = advances_since_feed >= PAGE_SIZE
            if fed:
                await repository.feed(parent.task_uuid, kind, target_size=target)
                advances_since_feed = 0
            actionable, waiting = await repository.open_work(
                parent.task_uuid, kind, after_id=after_id, limit=ADVANCE_BATCH_SIZE
            )
            if actionable:
                await repository.advance_many(parent.task_uuid, actionable)
                after_id = actionable[-1]
                advances_since_feed += len(actionable)
                continue
            # Refill before sleeping even when fewer than a page of transitions
            # completed. Only this coordinator advances its parent; no parallel
            # transactions compete for the same parent lock.
            if not fed:
                inserted = await repository.feed(
                    parent.task_uuid, kind, target_size=target
                )
                advances_since_feed = 0
                if inserted:
                    continue
            if waiting:
                # The shared notification/batch fallback releases every DB session.
                await runtime.wait_for_activities(
                    waiting, parent_task_uuid=parent.task_uuid, timeout=2
                )
                continue
            totals = await repository.totals(parent.task_uuid, kind)
            if totals["exhausted"]:
                return {
                    **totals,
                    "buffer_budget": settings["buffer_size"],
                    "buffer_target": target,
                }
    except TaskCancelledError:
        await repository.cancel_open(parent.task_uuid)
        raise
