"""Read-only progress and log lines for AI tasks that did not write a workbook."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from src.ai.task_progress import AI_TASK_TYPES, displayed_progress, task_type_name
from src.domain import RunStage
from src.domain.ai_work_item import AIWorkItem

_LEASE_SECONDS = 15 * 60
_RESULT_LIMIT = 2000
_ERROR_LIMIT = 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _entry(
    *,
    entry_id: int,
    entry_type: str,
    title: str,
    content: str,
    created_at: datetime,
    success: bool = True,
    model_used: str | None = None,
    error_message: str | None = None,
    duration: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "entry_uuid": None,
        "entry_type": entry_type,
        "title": title,
        "content": content,
        "content_type": "text",
        "prompt": None,
        "response": None,
        "model_used": model_used,
        "tokens_used": None,
        "cost": None,
        "duration": duration,
        "success": success,
        "error_message": error_message,
        "metadata": metadata or {},
        "created_at": created_at,
    }


def _lines(pairs: list[tuple[str, Any]]) -> str:
    rows: list[str] = []
    for label, value in pairs:
        if value is None or value == "":
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        rows.append(f"{label}: {text}")
    return "\n".join(rows)


def _scalar_block(value: Any) -> str | None:
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value.get("result"), dict):
        value = value["result"]
    if not isinstance(value, dict) or len(value) < 2:
        return None
    lines: list[str] = []
    for key, item in value.items():
        if isinstance(item, (dict, list)) or item is None:
            return None
        text = str(item)
        if len(text) > 160 or "://" in text or ":" in text:
            return None
        lines.append(f"{key}: {text}")
        if len(lines) > 16:
            return None
    return "\n".join(lines) if len(lines) >= 2 else None


def _result_text(output: Any) -> str | None:
    if not isinstance(output, dict) or not output:
        return None
    block = _scalar_block(output)
    if block:
        return block
    encoded = json.dumps(output, ensure_ascii=False, default=str, indent=2)
    if len(encoded) <= _RESULT_LIMIT:
        return encoded
    return encoded[:_RESULT_LIMIT] + "\n… output truncated …"


def _agent_name(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    agent = payload.get("agent_name")
    if not isinstance(agent, str):
        return None
    name = agent.strip()
    return name or None


def _duration(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None or finished is None:
        return None
    return round(max(0.0, (finished - started).total_seconds()), 1)


def latest_stage_counts(stages: list[Any]) -> dict[str, int]:
    """Status of the newest attempt of each stage name."""

    latest: dict[str, tuple[int, str]] = {}
    for stage in stages:
        name = str(getattr(stage, "stage", "") or "")
        if not name:
            continue
        attempt = int(getattr(stage, "attempt", 0) or 0)
        status = str(getattr(stage, "status", "") or "")
        current = latest.get(name)
        if current is None or attempt >= current[0]:
            latest[name] = (attempt, status)
    counts: dict[str, int] = {}
    for _, status in latest.values():
        counts[status] = counts.get(status, 0) + 1
    return counts


async def stage_counts(db: AsyncSession, task_uuids: list[str]) -> dict[str, dict[str, int]]:
    """Latest stage status counts for the task list. Skipped rows stay in the map."""

    if not task_uuids:
        return {}
    latest = (
        select(
            RunStage.task_uuid.label("task_uuid"),
            RunStage.stage.label("stage"),
            func.max(RunStage.attempt).label("attempt"),
        )
        .where(RunStage.task_uuid.in_(task_uuids))
        .group_by(RunStage.task_uuid, RunStage.stage)
        .subquery()
    )
    rows = (
        await db.execute(
            select(RunStage.task_uuid, RunStage.status, func.count())
            .join(
                latest,
                (RunStage.task_uuid == latest.c.task_uuid)
                & (RunStage.stage == latest.c.stage)
                & (RunStage.attempt == latest.c.attempt),
            )
            .group_by(RunStage.task_uuid, RunStage.status)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for parent, status, count in rows:
        counts.setdefault(str(parent), {})[str(status)] = int(count or 0)
    return counts


async def work_counts(db: AsyncSession, task_uuids: list[str]) -> dict[str, dict[str, int]]:
    if not task_uuids:
        return {}
    rows = (
        await db.execute(
            select(AIWorkItem.parent_task_uuid, AIWorkItem.status, func.count())
            .where(AIWorkItem.parent_task_uuid.in_(task_uuids))
            .group_by(AIWorkItem.parent_task_uuid, AIWorkItem.status)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for parent, status, count in rows:
        counts.setdefault(str(parent), {})[str(status)] = int(count or 0)
    return counts


def _work_item_query():
    return select(AIWorkItem).options(
        load_only(
            AIWorkItem.id,
            AIWorkItem.parent_task_uuid,
            AIWorkItem.line,
            AIWorkItem.status,
            AIWorkItem.attempt,
            AIWorkItem.route_key,
            AIWorkItem.error,
            AIWorkItem.payload,
            AIWorkItem.created_at,
            AIWorkItem.finished_at,
            AIWorkItem.lease_expires_at,
        )
    )


async def _work_items(db: AsyncSession, task_uuid: str) -> list[AIWorkItem]:
    attention = (
        (
            await db.execute(
                _work_item_query()
                .where(
                    AIWorkItem.parent_task_uuid == task_uuid,
                    AIWorkItem.status.in_(("failed", "cancelled", "leased")),
                )
                .order_by(AIWorkItem.id.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    recent = (
        (
            await db.execute(
                _work_item_query()
                .where(
                    AIWorkItem.parent_task_uuid == task_uuid,
                    AIWorkItem.status.in_(("succeeded", "queued")),
                )
                .order_by(AIWorkItem.id.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    # Keep the newest successes and the queued head; attention rows stay whole.
    succeeded = [item for item in recent if item.status == "succeeded"][:15]
    queued = [item for item in recent if item.status == "queued"][:10]
    return [*attention, *succeeded, *queued]


async def _stages(db: AsyncSession, task_uuid: str) -> list[RunStage]:
    return list(
        (
            await db.execute(
                select(RunStage)
                .where(RunStage.task_uuid == task_uuid)
                .order_by(RunStage.attempt, RunStage.id)
            )
        )
        .scalars()
        .all()
    )


def build_ai_task_entries(
    *,
    task_type: Any,
    status: Any,
    input_data: Any,
    output_data: Any,
    completed_at: datetime | None,
    counts: dict[str, int],
    items: list[Any],
    stages: list[Any],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Log lines the task drawer can render without a stored workbook row."""

    kind = task_type_name(task_type)
    if kind not in AI_TASK_TYPES and not counts:
        return []
    moment = now or _now()
    entries: list[dict[str, Any]] = []
    next_id = -1

    def take_id() -> int:
        nonlocal next_id
        current = next_id
        next_id -= 1
        return current

    for stage in stages:
        stage_status = str(getattr(stage, "status", "") or "")
        if stage_status == "skipped" and not getattr(stage, "error", None):
            continue
        metrics = getattr(stage, "metrics", None) or {}
        started = _aware(getattr(stage, "started_at", None)) or moment
        finished = _aware(getattr(stage, "finished_at", None))
        error = getattr(stage, "error", None)
        metric_rows = (
            [(str(key), value) for key, value in metrics.items()]
            if isinstance(metrics, dict)
            else []
        )
        content = _lines(
            [
                ("stage", getattr(stage, "stage", None)),
                ("status", stage_status),
                ("attempt", getattr(stage, "attempt", None)),
                *metric_rows,
                ("error", (str(error)[:_ERROR_LIMIT] if error else None)),
            ]
        )
        failed = stage_status == "failed" or bool(error)
        entries.append(
            _entry(
                entry_id=take_id(),
                entry_type="error" if failed else "info",
                title=f"Stage {getattr(stage, 'stage', 'stage')} {stage_status or 'recorded'}",
                content=content,
                created_at=finished or started,
                success=not failed,
                error_message=str(error)[:_ERROR_LIMIT] if error else None,
                duration=_duration(started, finished),
                metadata={"source": "run_stage", "attempt": getattr(stage, "attempt", None)},
            )
        )

    for item in items:
        item_status = str(getattr(item, "status", "") or "")
        created = _aware(getattr(item, "created_at", None)) or moment
        finished = _aware(getattr(item, "finished_at", None))
        lease_expires = _aware(getattr(item, "lease_expires_at", None))
        stamp = finished or created
        if item_status == "leased" and lease_expires is not None:
            stamp = lease_expires - timedelta(seconds=_LEASE_SECONDS)
        error = getattr(item, "error", None)
        error_text = str(error)[:_ERROR_LIMIT] if error else None
        route = getattr(item, "route_key", None)
        agent = _agent_name(getattr(item, "payload", None))
        failed = item_status in {"failed", "cancelled"} or bool(error_text)
        entry_type = "error" if item_status == "failed" else "warning" if failed or item_status == "cancelled" else "info"
        entries.append(
            _entry(
                entry_id=take_id(),
                entry_type=entry_type,
                title=f"Model call {item_status or 'recorded'}",
                content=_lines(
                    [
                        ("work_id", getattr(item, "id", None)),
                        ("line", getattr(item, "line", None)),
                        ("agent", agent),
                        ("route", route),
                        ("attempt", getattr(item, "attempt", None)),
                        ("status", item_status),
                        ("error", error_text),
                    ]
                ),
                created_at=stamp,
                success=item_status == "succeeded",
                model_used=str(route) if route else None,
                error_message=error_text if failed else None,
                duration=_duration(created, finished),
                metadata={"source": "ai_work_item", "work_id": getattr(item, "id", None)},
            )
        )

    result = _result_text(output_data)
    if result and kind in AI_TASK_TYPES:
        entries.append(
            _entry(
                entry_id=take_id(),
                entry_type="info",
                title="Task result",
                content=result,
                created_at=_aware(completed_at) or moment,
                metadata={"source": "output_data"},
            )
        )

    if counts:
        operation = None
        if isinstance(input_data, dict):
            raw_operation = input_data.get("operation")
            if isinstance(raw_operation, str) and raw_operation.strip():
                operation = raw_operation.strip()
        total = sum(counts.values())
        finished = sum(counts.get(name, 0) for name in ("succeeded", "failed", "cancelled"))
        failed_count = int(counts.get("failed") or 0) + int(counts.get("cancelled") or 0)
        entries.append(
            _entry(
                entry_id=take_id(),
                entry_type="warning" if failed_count else "info",
                title="Model work progress",
                content=_lines(
                    [
                        ("operation", operation),
                        ("queued", counts.get("queued", 0)),
                        ("leased", counts.get("leased", 0)),
                        ("succeeded", counts.get("succeeded", 0)),
                        ("failed", counts.get("failed", 0)),
                        ("cancelled", counts.get("cancelled", 0)),
                        ("finished", f"{finished}/{total}"),
                    ]
                ),
                created_at=moment,
                success=failed_count == 0,
                metadata={"source": "ai_work_summary"},
            )
        )
    return entries


def progress_for_task(
    *,
    task_type: Any,
    status: Any,
    stored: int | None,
    counts: dict[str, int] | None,
    stages: dict[str, int] | None = None,
) -> int:
    return displayed_progress(
        task_type=task_type,
        status=status,
        stored=stored,
        counts=counts,
        stages=stages,
    )


async def trace_for_task(db: AsyncSession, task: Any) -> tuple[int, list[dict[str, Any]]]:
    counts = (await work_counts(db, [task.task_uuid])).get(task.task_uuid, {})
    kind = task_type_name(task.task_type)
    entries: list[dict[str, Any]] = []
    stages: list[Any] = []
    if kind in AI_TASK_TYPES or counts:
        items = await _work_items(db, task.task_uuid) if counts else []
        stages = await _stages(db, task.task_uuid) if kind in AI_TASK_TYPES else []
        entries = build_ai_task_entries(
            task_type=task.task_type,
            status=task.status,
            input_data=task.input_data,
            output_data=task.output_data,
            completed_at=task.completed_at,
            counts=counts,
            items=items,
            stages=stages,
        )
    progress = progress_for_task(
        task_type=task.task_type,
        status=task.status,
        stored=task.progress,
        counts=counts,
        stages=latest_stage_counts(stages) if kind in AI_TASK_TYPES else None,
    )
    return progress, entries
