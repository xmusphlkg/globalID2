"""Progress shown for AI tasks, including model-work that never wrote task.progress."""

from __future__ import annotations

from typing import Any

AI_TASK_TYPES = frozenset(
    {
        "process_data",
        "generate_report",
        "generate_section",
        "review_section",
        "update_disease_knowledge",
        "agent_workflow",
        "enrich_literature",
        "discover_literature_gaps",
    }
)
_ACTIVE = frozenset({"pending", "queued", "running", "retrying"})
_TERMINAL_WORK = frozenset({"succeeded", "failed", "cancelled"})
_TERMINAL_STAGE = frozenset({"completed", "failed"})


def task_type_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def work_ratio(counts: dict[str, int]) -> int | None:
    """Share of model calls that have finished, or None when this task has none."""

    total = sum(int(count or 0) for count in counts.values())
    if total <= 0:
        return None
    finished = sum(int(counts.get(name) or 0) for name in _TERMINAL_WORK)
    return int(100 * finished / total)


def stage_ratio(counts: dict[str, int] | None) -> int | None:
    """Share of the latest collect/organize/store rows that have finished.

    Skipped stages are not work. None means this task has no stage rows.
    """

    if not counts:
        return None
    relevant = {
        str(name): int(count or 0)
        for name, count in counts.items()
        if str(name) != "skipped" and int(count or 0) > 0
    }
    total = sum(relevant.values())
    if total <= 0:
        return None
    finished = sum(relevant.get(name, 0) for name in _TERMINAL_STAGE)
    return int(100 * finished / total)


def displayed_progress(
    *,
    task_type: Any,
    status: Any,
    stored: int | None,
    counts: dict[str, int] | None = None,
    stages: dict[str, int] | None = None,
) -> int:
    """Progress the control panel should show.

    Agent workflows report model-call completion while they run, because those
    tasks otherwise stay at 0 until the row is gone. A finished AI task reads
    as 100. Other AI tasks keep the progress their own service already recorded.
    """

    stored_value = max(0, min(100, int(stored or 0)))
    kind = task_type_name(task_type)
    state = task_type_name(status)
    ratio = work_ratio(counts or {})
    stages_done = stage_ratio(stages)
    if kind == "agent_workflow" and ratio is not None and state in _ACTIVE:
        return min(99, ratio)
    if state == "completed" and kind in AI_TASK_TYPES and stored_value < 100:
        return 100
    if (
        kind == "agent_workflow"
        and ratio is not None
        and stored_value == 0
        and state in {"failed", "cancelled"}
    ):
        return ratio
    if (
        ratio is not None
        and stored_value == 0
        and kind in AI_TASK_TYPES
        and state in _ACTIVE
    ):
        return min(99, ratio)
    # Knowledge reconcile records stages and often never queues a model call.
    if (
        kind == "agent_workflow"
        and ratio is None
        and stages_done is not None
        and stored_value == 0
        and state in _ACTIVE | {"failed", "cancelled"}
    ):
        return min(99, stages_done) if state in _ACTIVE else stages_done
    return stored_value
