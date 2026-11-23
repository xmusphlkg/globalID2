"""Conservative recovery evidence for renamed collection runs."""

import json

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import Task, TaskStatus, TaskType


def _input_key(payload: dict) -> str:
    # Retain every option: a recent-window crawl cannot resolve a failed full
    # history import, and collection without storage cannot resolve a store error.
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


async def recovered_crawl_task_ids(db: AsyncSession, tasks: list[Task]) -> set[int]:
    candidates = [
        task for task in tasks
        if task.task_type == TaskType.CRAWL_DATA
        and task.country_id is not None
        and isinstance(task.input_data, dict)
        and task.input_data.get("source")
    ]
    if not candidates:
        return set()
    input_json = cast(Task.input_data, Text)
    # Aggregate in SQL so frequent scheduled collection does not load thousands
    # of completed tasks into every overview request. Decode only distinct inputs.
    rows = (await db.execute(
        select(Task.country_id, input_json, func.max(Task.id))
        .where(
            Task.task_type == TaskType.CRAWL_DATA,
            Task.status == TaskStatus.COMPLETED,
            *(~func.lower(Task.task_name).like(f"{prefix}%") for prefix in ("[test]", "[smoke]", "[smoke-slow]")),
            ~cast(Task.tags, Text).ilike('%"test"%'),
            Task.country_id.in_({task.country_id for task in candidates}),
            Task.input_data["source"].as_string().in_({task.input_data["source"] for task in candidates}),
            Task.id > min(task.id for task in candidates),
        )
        .group_by(Task.country_id, input_json)
    )).all()
    completed = {}
    for country_id, raw, task_id in rows:
        key = (country_id, _input_key(json.loads(raw)))
        completed[key] = max(task_id, completed.get(key, 0))
    return {
        task.id for task in candidates
        if completed.get((task.country_id, _input_key(task.input_data)), 0) > task.id
    }
