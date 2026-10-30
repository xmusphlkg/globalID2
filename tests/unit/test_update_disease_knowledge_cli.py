"""CLI submits durable knowledge tasks; workers retain custom catalogues."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import update_disease_knowledge as cli
from src.domain import TaskStatus, TaskType


@pytest.mark.parametrize("dry_run", [False, True])
async def test_cli_queues_and_waits_with_custom_catalogue(
    tmp_path, monkeypatch, capsys, dry_run
):
    catalogue = tmp_path / "custom.csv"
    catalogue.write_text(
        "disease_id,standard_name_en,standard_name_zh,category,icd_10,icd_11,description,source\n"
        "D901,Custom disease,自定义病,Bacterial,,,,local\n"
        "D902,Other disease,另一种病,Bacterial,,,,local\n"
    )
    create = AsyncMock(return_value=SimpleNamespace(task_uuid="queued-knowledge"))
    summary = {
        "disease_id": "D901",
        "fetched_sources": 2,
        "brief_statuses": {"en": "draft"},
    }
    wait = AsyncMock(return_value=summary)
    monkeypatch.setattr(cli.task_manager, "create_task", create)
    monkeypatch.setattr(cli, "wait_task_output", wait)
    # Any direct business execution would bypass task_context and is a regression.
    from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService

    direct = AsyncMock(side_effect=AssertionError("CLI called the business service"))
    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "update_disease", direct)
    arguments = [
        "--disease-csv",
        str(catalogue),
        "--disease-id",
        "d901",
        "--source",
        "who",
        "--source",
        "wikidata",
        "--generator",
        "ai",
        "--json",
    ]
    if dry_run:
        arguments.append("--dry-run")
    await cli.async_main(cli.build_parser().parse_args(arguments))

    create.assert_awaited_once()
    request = create.call_args.kwargs
    assert request["task_type"] == TaskType.UPDATE_DISEASE_KNOWLEDGE
    assert request["status"] == TaskStatus.QUEUED
    assert request["input_data"] == {
        "disease_id": "D901",
        "disease_csv_path": str(catalogue.resolve()),
        "source_groups": cli.expand_sources(["who", "wikidata"]),
        "force": dry_run,
        "generator": "ai",
        "dry_run": dry_run,
    }
    wait.assert_awaited_once_with("queued-knowledge")
    direct.assert_not_awaited()
    assert json.loads(capsys.readouterr().out) == [summary]


async def test_cli_preserves_force_limit_concurrency_and_per_disease_errors(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "load_standard_diseases",
        lambda path: [{"disease_id": f"D90{i}"} for i in range(5)],
    )
    monkeypatch.setattr(
        cli, "should_generate_public_disease_page", lambda disease: True
    )
    requests = []
    active, maximum = 0, 0

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(task_uuid=kwargs["input_data"]["disease_id"])

    async def wait(task_uuid):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            if task_uuid == "D901":
                raise RuntimeError("worker rejected update")
            return {"disease_id": task_uuid, "fetched_sources": 1}
        finally:
            active -= 1

    monkeypatch.setattr(cli.task_manager, "create_task", create)
    monkeypatch.setattr(cli, "wait_task_output", wait)
    args = cli.build_parser().parse_args(
        ["--force", "--limit", "3", "--concurrency", "2", "--json"]
    )
    await cli.async_main(args)
    results = json.loads(capsys.readouterr().out)
    assert maximum == 2 and len(requests) == 3
    assert all(request["input_data"]["force"] for request in requests)
    assert [result["disease_id"] for result in results] == ["D900", "D901", "D902"]
    assert results[1]["error"] == "worker rejected update"
    assert results[2]["fetched_sources"] == 1


@pytest.mark.parametrize("custom_path", [None, "/tmp/custom-knowledge-catalogue.csv"])
async def test_executor_constructs_knowledge_service_with_requested_catalogue(
    monkeypatch, custom_path
):
    from src.ai import work_queue
    from src.services import disease_knowledge_service, task_executor

    task = SimpleNamespace(
        id=7,
        task_uuid="knowledge",
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        input_data={"disease_csv_path": custom_path} if custom_path else {},
        priority="normal",
    )
    output = {"disease_id": "D901"}
    constructed = []

    class Service:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        async def execute_task(self, received):
            assert received is task
            assert work_queue.current_parent().task_uuid == task.task_uuid
            return output

    @asynccontextmanager
    async def lifecycle(received, **kwargs):
        async with work_queue.task_context(received):
            yield

    stored = SimpleNamespace(output_data=None)
    session = SimpleNamespace(get=AsyncMock(return_value=stored), commit=AsyncMock())

    @asynccontextmanager
    async def database():
        yield session

    monkeypatch.setattr(
        disease_knowledge_service, "DiseaseKnowledgeUpdateService", Service
    )
    monkeypatch.setattr(task_executor, "task_lifecycle", lifecycle)
    monkeypatch.setattr(task_executor, "get_database", database)
    assert await task_executor._run_disease_knowledge(task) == output
    assert constructed == (
        [{"disease_csv_path": Path(custom_path)}] if custom_path else [{}]
    )
    assert stored.output_data == output
