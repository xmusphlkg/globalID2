from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Response
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.api.routers.tasks import list_tasks
from src.core.database import get_engine, get_session_maker
from src.core.task_manager import task_manager
from src.domain.task import TaskPriority, TaskType


@pytest.mark.asyncio
async def test_list_tasks_search_matches_disease_id_in_input_data():
    try:
        task = await task_manager.create_task(
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
        )

        assert "D001" not in task.task_name
        assert task.description is not None
        assert "D001" not in task.description

        response = Response()
        session_maker = get_session_maker()
        async with session_maker() as db:
            items = await list_tasks(
                response=response,
                status=None,
                task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE.value,
                country_id=None,
                search="D001",
                limit=200,
                offset=0,
                db=db,
            )

        assert any(item.task_uuid == task.task_uuid for item in items)
        assert int(response.headers["X-Total-Count"]) >= 1
    finally:
        await get_engine().dispose()
