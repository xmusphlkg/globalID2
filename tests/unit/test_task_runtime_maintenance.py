from __future__ import annotations

import pytest
from types import SimpleNamespace

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


@pytest.mark.parametrize("replaced_ai_owner", [False, True])
async def test_restart_drains_and_recovers_both_roles_before_starting_scheduler(
    monkeypatch, replaced_ai_owner
):
    calls = []
    lease_reads = {}
    owners = {"worker": "worker-host-111-old", "ai-worker": "ai-worker-host-222-old"}

    async def lease(service):
        lease_reads[service] = lease_reads.get(service, 0) + 1
        if replaced_ai_owner and service == "ai-worker" and lease_reads[service] > 1:
            return "ai-worker-host-999-new", True
        return owners[service], True

    async def release(service, owner):
        calls.append(("release", service, owner))
        return True

    def property(unit, name):
        if name == "MainPID":
            return "111" if unit == restart_task_runtime.WORKER_UNIT else "222"
        return "success"

    async def stopped(unit, timeout):
        calls.append(("drained", unit))
        return "inactive"

    async def ready(service, pid, timeout):
        calls.append(("heartbeat", service))

    async def probe(timeout):
        return {"status": "ok", "task_queue": {}}

    monkeypatch.setattr(restart_task_runtime.runtime_registry, "lease_owner", lease)
    monkeypatch.setattr(
        restart_task_runtime.runtime_registry, "release_stopped_instance", release
    )
    monkeypatch.setattr(restart_task_runtime, "_unit_property", property)
    monkeypatch.setattr(restart_task_runtime, "_wait_unit_stopped", stopped)
    monkeypatch.setattr(restart_task_runtime, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(
        restart_task_runtime,
        "_run_systemctl",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        restart_task_runtime,
        "_recover_owner",
        lambda owner: calls.append(("recover", owner)),
    )
    monkeypatch.setattr(restart_task_runtime, "_wait_runtime_service", ready)
    monkeypatch.setattr(restart_task_runtime, "_wait_ready_probe", probe)
    args = SimpleNamespace(
        include_api=False, drain_timeout_seconds=60, readiness_timeout_seconds=10
    )
    if replaced_ai_owner:
        with pytest.raises(RuntimeError, match="different ai-worker"):
            await restart_task_runtime._restart(args)
        assert not any(call[0] in {"recover", "start"} for call in calls)
        return
    assert await restart_task_runtime._restart(args) == 0
    stop = (
        "stop",
        "--no-block",
        restart_task_runtime.WORKER_UNIT,
        restart_task_runtime.AI_WORKER_UNIT,
    )
    assert stop in calls
    assert {call[1] for call in calls if call[0] == "recover"} == set(owners.values())
    latest_release = max(i for i, call in enumerate(calls) if call[0] == "release")
    earliest_recover = min(i for i, call in enumerate(calls) if call[0] == "recover")
    assert latest_release < earliest_recover
    scheduler_start = calls.index(("start", restart_task_runtime.SCHEDULER_UNIT))
    assert calls.index(("heartbeat", "worker")) < scheduler_start
    assert calls.index(("heartbeat", "ai-worker")) < scheduler_start
