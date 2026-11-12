"""Durable activity lifecycle constants shared by storage and execution."""

from dataclasses import dataclass

LEASE_SECONDS = 60
EXECUTION_DEADLINE_SECONDS = 15 * 60
MAX_ATTEMPTS = 3
# Maximum age while queued, measured from original submission across retries.
# It is independent of an already leased provider execution deadline.
QUEUE_WAIT_SECONDS = 6 * 60 * 60
CLAIM_SCAN_PAGE_SIZE = 64
HEALTH_PROBE_PERMITS = 4
RUNTIME_PARENT = "runtime"
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
# Business organizers decide how an individual deferred activity affects their run.
FAILURE_THRESHOLDS = {
    "situation_notice": None,
    "knowledge_brief": 0,
    "literature_enrich": 0,
    "report_section": 0,
    "report_review": 0,
    "content_governance": None,
}


class AIWorkFailed(RuntimeError):
    """A durable activity exhausted its execution policy."""


@dataclass(frozen=True)
class RoutingRequirements:
    """Business constraints; the scheduler chooses the actual route at claim."""

    avoid_models: tuple[str, ...] = ()
    preferred_models: tuple[str, ...] = ()
    require_distinct_model: bool = False
    capability: str = "text"


def parent_cancelled(parent, *, parent_task_uuid: str) -> bool:
    """Business activities cannot outlive a deleted or terminal parent task."""
    if parent_task_uuid == RUNTIME_PARENT:
        return False
    return parent is None or (
        getattr(parent.status, "value", parent.status)
        in {"cancelled", "failed", "completed"}
        or bool((parent.metadata_ or {}).get("cancel_requested"))
    )


def activity_cancelled(parent, item) -> bool:
    if item.cancel_requested or parent_cancelled(
        parent, parent_task_uuid=item.parent_task_uuid
    ):
        return True
    if item.parent_task_uuid == RUNTIME_PARENT:
        return False
    return int((item.payload or {}).get("execution_generation", 0)) != int(
        (parent.metadata_ or {}).get("ai_execution_generation", 0)
    )
