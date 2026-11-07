import asyncio
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor

import pytest

from src.generation import country_projection_workers as workers


def test_worker_budget_reserves_parent_memory(monkeypatch):
    monkeypatch.setattr(workers.os, "cpu_count", lambda: 16)
    for gib, expected in ((None, 1), (4, 1), (8, 1), (16, 3), (60, 8)):
        monkeypatch.setattr(workers, "_read_cgroup_memory_limit_bytes", lambda size=gib: size * 1024 ** 3 if size else None)
        assert workers.default_country_workers() == expected


async def test_cancelled_process_group_joins_all_readers_before_return(monkeypatch):
    executor = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
    original_submit = executor.submit
    def sleeping_reader(_function, _request):
        return original_submit(time.sleep, 60)
    monkeypatch.setattr(executor, "submit", sleeping_reader)
    monkeypatch.setattr(workers, "ProcessPoolExecutor", lambda **_: executor)
    monkeypatch.setenv(workers.SNAPSHOT_ENV, "00000003-00000001-1")
    task = asyncio.create_task(workers.project_countries([{"country": {}} for _ in range(2)], workers=2))
    deadline = time.monotonic() + 5
    while len(executor._processes) != 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    processes = list(executor._processes.values())
    assert len(processes) == 2 and all(process.is_alive() for process in processes)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(not process.is_alive() for process in processes)


async def test_parallel_projection_requires_exported_database_snapshot(monkeypatch):
    monkeypatch.delenv(workers.SNAPSHOT_ENV, raising=False)
    monkeypatch.setattr(workers, "ProcessPoolExecutor", lambda **_: (_ for _ in ()).throw(AssertionError("must validate before spawning")))
    with pytest.raises(ValueError, match="Invalid PostgreSQL"):
        await workers.project_countries([], workers=2)
