import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core.config import TaskWorkerSettings
from dashboard.api.routers.tasks import _worker_concurrency


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
