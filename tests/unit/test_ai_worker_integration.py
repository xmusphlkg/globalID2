"""Role-specific capacity and scheduling readiness, without external services."""

import asyncio
from types import SimpleNamespace

import pytest

from src.control_plane import operations, scheduler


@pytest.mark.parametrize("ai_live", [False, True])
async def test_dashboard_ai_capacity_comes_from_ai_worker_only(monkeypatch, ai_live):
    async def services():
        rows = [
            {"service": "worker", "pid": 11, "metadata": {"ai_concurrency_current": 99}}
        ]
        if ai_live:
            rows.extend(
                [
                    {
                        "service": "ai-worker",
                        "pid": 12,
                        "last_seen_at": "2026-11-10",
                        "metadata": {"ai_concurrency_current": 1},
                    },
                    {
                        "service": "ai-worker",
                        "pid": 13,
                        "last_seen_at": "2026-11-11",
                        "metadata": {
                            "ai_concurrency_current": 12,
                            "ai_concurrency_max": 16,
                        },
                    },
                ]
            )
        return rows, True

    class Database:
        async def execute(self, _query):
            return SimpleNamespace(
                one=lambda: SimpleNamespace(
                    queued_tasks=2,
                    running_tasks=3,
                    retrying_tasks=0,
                    latest_created_at=None,
                    latest_started_at=None,
                    latest_completed_at=None,
                )
            )

    monkeypatch.setattr(operations.runtime_registry, "list_services", services)
    result = await operations.TaskQueryRepository(Database()).worker_status(16)
    assert result["worker_pid"] == 11
    assert result["worker_process_running"]
    assert result["ai_concurrency_current"] == (12 if ai_live else 0)


async def test_scheduler_stops_when_ai_worker_disappears(monkeypatch):
    checked = []

    async def live(service):
        checked.append(service)
        return service == "worker", True

    monkeypatch.setattr(scheduler.runtime_registry, "service_is_live", live)
    stopped, unavailable = asyncio.Event(), asyncio.Event()
    await scheduler._monitor_worker(stopped, unavailable, grace_seconds=0)
    assert stopped.is_set() and unavailable.is_set()
    assert set(checked) == {"worker", "ai-worker"}
