"""Real spawn workers preserve source results and drain on cancellation."""
import asyncio
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select

from src.data.crawlers.br import SINANFile
from src.data.processors.br import BRMonthlyUpdater
from src.core.task_manager import TaskManager
from src.domain import Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty, RunStage
from src.services.ingestion import process_organize as workers, runner
from src.services.ingestion.countries import br
from src.services.exceptions import TaskCancelledError
from tests.unit.test_cn_us_jp_crawl_pipelines import db, inputs

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/BR/AIDABR25.dbc"


def _process_id(_request):
    return os.getpid()


def _wait_with_temporary(marker):
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "source.dbf").write_bytes(b"temporary parsed bytes")
        Path(marker).write_text(directory)
        time.sleep(30)


def _organize_fixture(request):
    # This repository fixture contains synthetic, uncompressed DBF bytes. Keep
    # the real date/count aggregation in the spawned process and stub only DBC.
    with patch.object(br.BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(shutil.copyfile)):
        return br._organize_process(request)


async def test_parser_runs_in_another_process():
    assert await workers.organize_in_process(_process_id, None) != os.getpid()


async def test_cancelled_parser_is_terminated_and_joined(tmp_path, monkeypatch):
    executor_type = workers.ProcessPoolExecutor
    captured = []
    temporary_roots = []

    def create(**kwargs):
        executor = executor_type(**kwargs)
        captured.append(executor)
        temporary_roots.append(Path(kwargs["initargs"][0]))
        return executor

    monkeypatch.setattr(workers, "ProcessPoolExecutor", create)
    marker = tmp_path / "started"
    task = asyncio.create_task(workers.organize_in_process(_wait_with_temporary, marker))
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert marker.exists()
    assert Path(marker.read_text()).is_dir()
    processes = list(captured[0]._processes.values())
    assert len(processes) == 1 and processes[0].is_alive()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(not process.is_alive() for process in processes)
    assert all(not path.exists() for path in temporary_roots)


async def test_concurrency_is_bounded_and_waiting_cancellation_spawns_nothing(monkeypatch):
    monkeypatch.setitem(workers._limits, asyncio.get_running_loop(), asyncio.Semaphore(1))
    limit = workers._limit()
    await limit.acquire()
    create = Mock(side_effect=AssertionError("must wait for CPU slot"))
    monkeypatch.setattr(workers, "ProcessPoolExecutor", create)
    task = asyncio.create_task(workers.organize_in_process(_process_id, None))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    create.assert_not_called()
    limit.release()


async def test_br_spawn_keeps_national_aggregation_identical(tmp_path, monkeypatch):
    payload = FIXTURE.read_bytes()
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/br/source.dbc")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    item = SINANFile("AIDA", "AIDS in adults", 2025, "AIDABR25.dbc",
                     "ftp://fixture/AIDABR25.dbc", "final", len(payload), None)
    artifact = SimpleNamespace(
        relative_path=path.as_posix(), byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(), kind="sinan_dbc",
        source_url=item.url, metadata_={"file": asdict(item)},
    )
    updater = BRMonthlyUpdater(output_csv=tmp_path / "history.csv")
    options = dict(source="all", months=[(2025, 1), (2025, 2)], history_csv=updater.output_csv)
    with patch.object(br.BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(shutil.copyfile)):
        expected = br.organize([artifact], updater=updater, **options)
    monkeypatch.setattr(br, "_organize_process", _organize_fixture)
    actual = await br.organize_async([artifact], updater=updater, **options)
    assert asdict(actual) == asdict(expected)
    assert actual.rows and actual.source_audit


async def test_br_process_keeps_artifact_integrity_checks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/br/bad.dbc")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"modified")
    artifact = SimpleNamespace(relative_path=path.as_posix(), byte_size=1, sha256="0" * 64,
                               kind="history_csv", source_url="fixture", metadata_={})
    updater = BRMonthlyUpdater(output_csv=tmp_path / "history.csv")
    with pytest.raises(ValueError, match="Artifact integrity check failed"):
        await br.organize_async([artifact], updater=updater, source="all",
                                months=[(2025, 1)], history_csv=updater.output_csv)


async def test_runner_awaits_async_parser_before_store(db):
    params = inputs()
    fetched = SimpleNamespace(rows=[{"Cases": "3"}],
                              series_rows=[{"Cases": "3", "ReportingArea": "US RESIDENTS"}])
    params["connector"].organize_async = AsyncMock(return_value=fetched)
    params["connector"].organize = Mock(side_effect=AssertionError("sync fallback used"))
    await runner.run_surveillance(**params)
    params["connector"].organize_async.assert_awaited_once()
    params["connector"].organize.assert_not_called()
    params["service"]._import_rows_with_series.assert_awaited_once()
    async with db() as session:
        stage = (await session.execute(select(RunStage).where(RunStage.stage == "organize"))).scalar_one()
        assert stage.status == "completed" and stage.metrics["rows"] == 1


async def test_dashboard_cancel_interrupts_running_parser_before_store(db, tmp_path, monkeypatch):
    params = inputs()
    async with db.kw["bind"].begin() as connection:
        await connection.run_sync(Task.__table__.create)
    async with db() as session:
        session.add(Task(task_uuid=params["task"].task_uuid, task_type=TaskType.CRAWL_DATA,
                         task_name="CPU parser", status=TaskStatus.RUNNING))
        await session.commit()
    manager = runner.task_manager
    monkeypatch.setattr(manager, "is_cancel_requested", TaskManager.is_cancel_requested.__get__(manager))
    monkeypatch.setattr(manager, "_broadcast", AsyncMock())
    monkeypatch.setattr(manager, "add_workbook_entry", AsyncMock())
    monkeypatch.setattr(runner, "ORGANIZE_CANCEL_POLL_SECONDS", 0.01)
    marker = tmp_path / "parser-started"
    pools, roots = [], []
    executor_type = workers.ProcessPoolExecutor

    def create(**kwargs):
        executor = executor_type(**kwargs)
        pools.append(executor)
        roots.append(Path(kwargs["initargs"][0]))
        return executor

    monkeypatch.setattr(workers, "ProcessPoolExecutor", create)

    async def organize(*_args, **_kwargs):
        return await workers.organize_in_process(_wait_with_temporary, marker)

    params["connector"].organize_async = organize
    task = asyncio.create_task(runner.run_surveillance(**params))
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert marker.exists()
    processes = list(pools[0]._processes.values())
    # Use the actual Dashboard service path, which persists a cooperative flag.
    await manager.request_task_cancel(params["task"].task_uuid)
    with pytest.raises(TaskCancelledError):
        await asyncio.wait_for(task, timeout=3)
    assert all(not process.is_alive() for process in processes)
    assert all(not path.exists() for path in roots)
    params["service"]._import_rows_with_series.assert_not_awaited()
    async with db() as session:
        stages = list((await session.execute(select(RunStage))).scalars())
        assert next(row for row in stages if row.stage == "organize").status == "failed"
        assert next(row for row in stages if row.stage == "store").status == "skipped"
        assert not list((await session.execute(select(PublishDirty))).scalars())
