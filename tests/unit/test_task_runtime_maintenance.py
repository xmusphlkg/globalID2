from __future__ import annotations

import pytest

from scripts import restart_task_runtime


@pytest.mark.asyncio
async def test_runtime_wait_ignores_stale_heartbeat_from_previous_pid(monkeypatch):
    calls = 0

    async def list_services():
        nonlocal calls
        calls += 1
        pid = 111 if calls == 1 else 222
        return ([{"service": "worker", "pid": pid}], True)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        restart_task_runtime.runtime_registry, "list_services", list_services
    )
    monkeypatch.setattr(restart_task_runtime.asyncio, "sleep", no_sleep)

    await restart_task_runtime._wait_runtime_service("worker", 222, 10)

    assert calls == 2


@pytest.mark.asyncio
async def test_ready_probe_retries_transient_socket_startup(monkeypatch):
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("socket not listening yet")
        return {"status": "ok", "task_queue": {"queued": 0, "running": 0}}

    async def no_sleep(_seconds):
        return None

    async def direct_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(restart_task_runtime, "_ready_probe", probe)
    monkeypatch.setattr(restart_task_runtime.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(restart_task_runtime.asyncio, "to_thread", direct_to_thread)

    result = await restart_task_runtime._wait_ready_probe(10)

    assert calls == 2
    assert result["status"] == "ok"
