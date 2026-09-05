import os
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core.config import TaskWorkerSettings
from src.domain import TaskStatus, TaskType
import src.core.task_manager as task_manager_module
from src.core.task_manager import task_manager
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
                "TASK_WORKER_AI_CONCURRENCY=4",
                "TASK_WORKER_AI_DYNAMIC_CONCURRENCY_ENABLED=true",
                "TASK_WORKER_AI_CONCURRENCY_MIN=1",
                "TASK_WORKER_AI_CONCURRENCY_PER_ROUTE=2",
                "TASK_WORKER_AI_CONCURRENCY_SCALE_UP_SUCCESSES=2",
                "TASK_WORKER_AI_CONCURRENCY_ADJUST_SECONDS=10",
                "TASK_WORKER_KNOWLEDGE_SOURCE_CONCURRENCY=3",
                "TASK_WORKER_POLL_INTERVAL=1.5",
                "TASK_WORKER_IDLE_LOG_EVERY=12",
                "TASK_WORKER_TASK_HEARTBEAT_SECONDS=10",
                "TASK_WORKER_SHUTDOWN_GRACE_SECONDS=45",
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
    assert settings.ai_concurrency == 4
    assert settings.ai_dynamic_concurrency_enabled is True
    assert settings.ai_concurrency_min == 1
    assert settings.ai_concurrency_per_route == 2
    assert settings.ai_concurrency_scale_up_successes == 2
    assert settings.ai_concurrency_adjust_seconds == 10
    assert settings.knowledge_source_concurrency == 3
    assert settings.poll_interval_seconds == 1.5
    assert settings.idle_log_every == 12
    assert settings.task_heartbeat_seconds == 10
    assert settings.shutdown_grace_seconds == 45
    assert settings.stale_task_seconds == 120
    assert settings.recovery_scan_seconds == 30
    assert settings.runtime_lease_ttl_seconds == 240
    assert settings.runtime_heartbeat_ttl_seconds == 150
    assert settings.scheduler_worker_grace_seconds == 60


def test_worker_concurrency_uses_unified_config(monkeypatch):
    fake_config = SimpleNamespace(task_worker=SimpleNamespace(concurrency=7))
    monkeypatch.setattr("dashboard.api.routers.tasks.get_config", lambda: fake_config)

    assert _worker_concurrency() == 7


def test_adaptive_ai_concurrency_uses_routes_and_aimd_backoff():
    async def routes():
        return [
            {"available_for_routing": True, "provider_id": 1},
            {"available_for_routing": True, "provider_id": 2},
            {"available_for_routing": True, "provider_id": 2},
        ]

    controller = task_worker.AdaptiveAIConcurrencyController(
        minimum=1,
        maximum=6,
        enabled=True,
        slots_per_route=2,
        scale_up_successes=2,
        adjust_seconds=1,
        route_loader=routes,
    )

    assert asyncio.run(controller.refresh(force=True)) == 2
    controller.record_result(success=True)
    assert controller.capacity == 2
    controller.record_result(success=True)
    assert controller.capacity == 3
    controller.record_result(success=False, error=RuntimeError("Agent completion failed after route timeout"))
    assert controller.capacity == 2


def test_adaptive_ai_concurrency_tracks_model_center_provider_capacity():
    async def routes():
        return [
            {"available_for_routing": True, "provider_id": 1, "runtime_provider_capacity": 1},
            {"available_for_routing": True, "provider_id": 1, "runtime_provider_capacity": 1},
            {"available_for_routing": True, "provider_id": 2, "runtime_provider_capacity": 2},
        ]

    controller = task_worker.AdaptiveAIConcurrencyController(
        minimum=1,
        maximum=6,
        enabled=True,
        slots_per_route=2,
        scale_up_successes=2,
        adjust_seconds=1,
        route_loader=routes,
    )

    assert asyncio.run(controller.refresh(force=True)) == 2
    controller.record_result(success=True)
    controller.record_result(success=True)
    assert controller.capacity == 3

    async def reduced_routes():
        return [{"available_for_routing": True, "provider_id": 1, "runtime_provider_capacity": 1}]

    controller.route_loader = reduced_routes
    assert asyncio.run(controller.refresh(force=True)) == 1


def test_adaptive_ai_concurrency_pauses_when_model_center_has_no_routes():
    async def routes():
        return []

    controller = task_worker.AdaptiveAIConcurrencyController(
        minimum=1,
        maximum=6,
        enabled=True,
        slots_per_route=2,
        scale_up_successes=2,
        adjust_seconds=1,
        route_loader=routes,
    )

    assert asyncio.run(controller.refresh(force=True)) == 0
    assert controller.record_result(success=False, error=RuntimeError("connection error")) == 0


def test_release_export_and_literature_ai_tasks_do_not_overlap():
    assert task_worker._release_memory_blocked_task_types([TaskType.EXPORT_DATA]) >= {
        TaskType.ENRICH_LITERATURE,
        TaskType.SYNC_LITERATURE,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
    }
    assert task_worker._release_memory_blocked_task_types([TaskType.ENRICH_LITERATURE]) == {
        TaskType.EXPORT_DATA
    }
    assert task_worker._release_memory_blocked_task_types([], release_waiting=True) >= {
        TaskType.ENRICH_LITERATURE,
        TaskType.SYNC_LITERATURE,
        TaskType.UPDATE_DISEASE_KNOWLEDGE,
    }
    assert task_worker._release_memory_blocked_task_types([TaskType.CRAWL_DATA]) == set()


def test_knowledge_task_disease_id_uses_single_task_resource():
    direct = SimpleNamespace(input_data={"disease_id": "d018"})
    fallback = SimpleNamespace(input_data={"disease_ids": ["d019"]})
    ambiguous = SimpleNamespace(input_data={"disease_ids": ["d020", "d021"]})

    assert task_worker._knowledge_task_disease_id(direct) == "D018"
    assert task_worker._knowledge_task_disease_id(fallback) == "D019"
    assert task_worker._knowledge_task_disease_id(ambiguous) is None


def test_controlled_restart_requeues_only_resumable_tasks(monkeypatch):
    calls = []

    async def fake_requeue(task_uuid, owner, reason):
        calls.append(("requeue", task_uuid, owner, reason))
        return task_uuid == "knowledge-task"

    async def fake_workbook(task_uuid, **kwargs):
        calls.append(("workbook", task_uuid, kwargs["title"]))

    monkeypatch.setattr(
        task_worker.task_manager,
        "requeue_owned_task_lease",
        fake_requeue,
    )
    monkeypatch.setattr(
        task_worker.task_manager,
        "add_workbook_entry",
        fake_workbook,
    )

    requeued = asyncio.run(
        task_worker._requeue_interrupted_tasks_for_restart(
            [
                ("knowledge-task", TaskType.UPDATE_DISEASE_KNOWLEDGE),
                ("export-task", TaskType.EXPORT_DATA),
            ],
            "worker-1",
        )
    )

    assert requeued == 1
    assert [call[:2] for call in calls] == [
        ("requeue", "knowledge-task"),
        ("workbook", "knowledge-task"),
    ]


def test_controlled_restart_keeps_requested_cancellation_terminal(monkeypatch):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        task_uuid="cancelled-knowledge-task",
        status=TaskStatus.RUNNING,
        progress=55,
        started_at=now,
        completed_at=None,
        actual_duration=None,
        last_error=None,
        metadata_={
            "cancel_requested": True,
            "cancel_reason": "Catalogue item is non-public.",
            "task_lease": {"owner": "worker-1"},
        },
    )

    class Result:
        def scalar_one_or_none(self):
            return task

    class Database:
        commits = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return Result()

        async def commit(self):
            self.commits += 1

    database = Database()
    broadcasts = []
    monkeypatch.setattr(task_manager_module, "get_db", lambda: database)

    async def broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(task_manager, "_broadcast", broadcast)

    requeued = asyncio.run(
        task_manager.requeue_owned_task_lease(
            "cancelled-knowledge-task",
            "worker-1",
            "Released after controlled restart.",
        )
    )

    assert requeued is False
    assert task.status == TaskStatus.CANCELLED
    assert task.last_error == "Catalogue item is non-public."
    assert task.metadata_["task_lease"]["terminal_status"] == "cancelled"
    assert broadcasts[0]["status"] == "cancelled"


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
