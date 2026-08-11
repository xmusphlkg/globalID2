from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Response
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.api.routers.tasks import list_tasks
from src.core.database import get_engine, get_session_maker
from src.domain.task import Task, TaskPriority, TaskType


@pytest.mark.asyncio
async def test_list_tasks_search_matches_disease_id_in_input_data():
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            task = Task(
                task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
                task_name="[test] knowledge search regression",
                priority=TaskPriority.LOW,
                description="Created for /tasks search regression coverage",
                input_data={
                    "disease_id": "D001",
                    "disease_ids": ["D001"],
                    "source_groups": ["who"],
                    "source": ["who"],
                    "force": True,
                    "generator": "ai",
                },
                tags=["test"],
            )
            db.add(task)
            await db.flush()

            assert "D001" not in task.task_name
            assert task.description is not None
            assert "D001" not in task.description

            response = Response()
            items = await list_tasks(
                response=response,
                status=None,
                task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE.value,
                country_code=None,
                search="D001",
                page=1,
                page_size=200,
                db=db,
            )
            assert any(item.task_uuid == task.task_uuid for item in items)
            assert int(response.headers["X-Total-Count"]) >= 1
            await db.rollback()
    finally:
        await get_engine().dispose()
