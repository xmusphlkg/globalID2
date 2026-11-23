from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import restart_task_runtime


@pytest.fixture
def virtual_clock(monkeypatch):
    clock = SimpleNamespace(elapsed=0.0)
    real_sleep = restart_task_runtime.asyncio.sleep

    async def advance(seconds):
        clock.elapsed += seconds
        # Yield so concurrent timers would begin while the first unit drains.
        await real_sleep(0)

    monkeypatch.setattr(restart_task_runtime, "time", SimpleNamespace(
        monotonic=lambda: clock.elapsed,
    ))
    monkeypatch.setattr(restart_task_runtime.asyncio, "sleep", advance)
    return clock


@pytest.mark.parametrize("terminal_state", ["inactive", "failed"])
async def test_worker_stop_observed_at_deadline_is_not_reported_as_timeout(
    monkeypatch, virtual_clock, terminal_state,
):
    monkeypatch.setattr(restart_task_runtime, "_unit_property", lambda *_: (
        terminal_state if virtual_clock.elapsed >= 30 else "deactivating"
    ))
    assert await restart_task_runtime._wait_unit_stopped("worker.service", 30) == terminal_state
    assert virtual_clock.elapsed == 30


async def test_worker_drain_timeout_stays_bounded_without_forcing_stop(monkeypatch, virtual_clock):
    calls = []
    monkeypatch.setattr(restart_task_runtime, "_unit_property", lambda *_: "deactivating")
    monkeypatch.setattr(restart_task_runtime, "_run_systemctl", lambda *args: calls.append(args))
    with pytest.raises(TimeoutError, match="worker.service.*no force kill"):
        await restart_task_runtime._wait_unit_stopped("worker.service", 30)
    assert virtual_clock.elapsed == 30
    assert calls == []


def test_maintenance_timeout_leaves_scheduler_stopped(monkeypatch, capsys):
    calls = []

    async def timeout(_args):
        raise TimeoutError("worker still draining")

    monkeypatch.setattr(restart_task_runtime, "_restart", timeout)
    monkeypatch.setattr(restart_task_runtime, "_run_systemctl", lambda *args: calls.append(args))
    monkeypatch.setattr(restart_task_runtime, "_unit_property", lambda *_: "inactive")
    assert restart_task_runtime.main([]) == 2
    assert calls == [("stop", restart_task_runtime.SCHEDULER_UNIT)]
    assert json.loads(capsys.readouterr().out) == {
        "event": "maintenance_failed", "error_type": "TimeoutError", "scheduler_left_stopped": True,
    }


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
@pytest.mark.parametrize("dependency_delayed_stop", [False, True])
async def test_restart_drains_and_recovers_both_roles_before_starting_scheduler(
    monkeypatch, replaced_ai_owner, dependency_delayed_stop, virtual_clock,
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
        if name == "ActiveState":
            if unit == restart_task_runtime.WORKER_UNIT:
                return "deactivating" if virtual_clock.elapsed < 56 else "inactive"
            # The AI stop is waiting in systemd until the general unit exits,
            # then needs another 56 seconds of its own 60-second drain budget.
            if virtual_clock.elapsed < 56:
                return "active"
            return "deactivating" if virtual_clock.elapsed < 112 else "inactive"
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
    if not dependency_delayed_stop:
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
    if dependency_delayed_stop:
        assert virtual_clock.elapsed == 112
    stop = (
        "stop",
        "--no-block",
        restart_task_runtime.WORKER_UNIT,
        restart_task_runtime.AI_WORKER_UNIT,
    )
    assert stop in calls
    assert not any(call[0] == "kill" for call in calls)
    assert {call[1] for call in calls if call[0] == "recover"} == set(owners.values())
    latest_release = max(i for i, call in enumerate(calls) if call[0] == "release")
    earliest_recover = min(i for i, call in enumerate(calls) if call[0] == "recover")
    assert latest_release < earliest_recover
    scheduler_start = calls.index(("start", restart_task_runtime.SCHEDULER_UNIT))
    assert calls.index(("heartbeat", "worker")) < scheduler_start
    assert calls.index(("heartbeat", "ai-worker")) < scheduler_start
