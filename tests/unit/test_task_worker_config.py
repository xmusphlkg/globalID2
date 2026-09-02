import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core.config import TaskWorkerSettings
from dashboard.api.routers.tasks import _worker_concurrency
from src.services import task_worker


def test_task_worker_idle_logging_defaults_to_low_noise():
    settings = TaskWorkerSettings()

    assert settings.poll_interval_seconds == 2.0
    assert settings.idle_log_every == 300


def test_task_worker_settings_reads_env_aliases(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TASK_WORKER_CONCURRENCY=6",
                "TASK_WORKER_POLL_INTERVAL=1.5",
                "TASK_WORKER_IDLE_LOG_EVERY=12",
                "TASK_WORKER_TASK_HEARTBEAT_SECONDS=10",
                "TASK_WORKER_STALE_TASK_SECONDS=120",
                "TASK_WORKER_RECOVERY_SCAN_SECONDS=30",
                "TASK_WORKER_RUNTIME_LEASE_TTL_SECONDS=240",
                "TASK_WORKER_RUNTIME_HEARTBEAT_TTL_SECONDS=150",
                "SCHEDULER_WORKER_GRACE_SECONDS=60",
            ]
        ),
        encoding="utf-8",
    )

    settings = TaskWorkerSettings(_env_file=env_file)

    assert settings.concurrency == 6
    assert settings.poll_interval_seconds == 1.5
    assert settings.idle_log_every == 12
    assert settings.task_heartbeat_seconds == 10
    assert settings.stale_task_seconds == 120
    assert settings.recovery_scan_seconds == 30
    assert settings.runtime_lease_ttl_seconds == 240
    assert settings.runtime_heartbeat_ttl_seconds == 150
    assert settings.scheduler_worker_grace_seconds == 60


def test_worker_concurrency_uses_unified_config(monkeypatch):
    fake_config = SimpleNamespace(task_worker=SimpleNamespace(concurrency=7))
    monkeypatch.setattr("dashboard.api.routers.tasks.get_config", lambda: fake_config)

    assert _worker_concurrency() == 7


def test_release_task_memory_collects_garbage_without_linux_trim(monkeypatch):
    calls = []

    monkeypatch.setattr(task_worker.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(task_worker.sys, "platform", "darwin")
    monkeypatch.setattr(
        task_worker.ctypes.util,
        "find_library",
        lambda _name: calls.append("lookup"),
    )

    task_worker._release_task_memory()

    assert calls == ["gc"]


def test_release_task_memory_trims_linux_libc_heap(monkeypatch):
    class FakeMallocTrim:
        def __init__(self):
            self.calls = []

        def __call__(self, value):
            self.calls.append(value)
            return 1

    class FakeLibC:
        def __init__(self):
            self.malloc_trim = FakeMallocTrim()

    fake_libc = FakeLibC()
    calls = []

    monkeypatch.setattr(task_worker, "_LIBC", None)
    monkeypatch.setattr(task_worker, "_LIBC_LOOKUP_DONE", False)
    monkeypatch.setattr(task_worker.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(task_worker.sys, "platform", "linux")
    monkeypatch.setattr(task_worker.ctypes.util, "find_library", lambda _name: "libc.so")
    monkeypatch.setattr(task_worker.ctypes, "CDLL", lambda _name: fake_libc)

    task_worker._release_task_memory()

    assert calls == ["gc"]
    assert fake_libc.malloc_trim.calls == [0]
