from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.control_plane import health as health_module


class _Database:
    def __init__(self, scalar_values):
        self.scalar_values = list(scalar_values)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query):
        return None

    async def scalar(self, _query):
        return self.scalar_values.pop(0)


class _Cutover:
    def operational_summary(self):
        return {"mode": "test"}


@pytest.mark.asyncio
async def test_readiness_reports_runtime_and_queue_state(monkeypatch):
    oldest = datetime.now(timezone.utc) - timedelta(minutes=5)
    database = _Database([3, 1, oldest])

    async def list_services():
        return (
            [{"service": "worker", "instance_id": "worker-1", "status": "healthy"}],
            True,
        )

    monkeypatch.setattr(health_module, "get_database", lambda: database)
    monkeypatch.setattr(health_module.runtime_registry, "list_services", list_services)
    monkeypatch.setattr(health_module, "get_disease_cutover_config", lambda: _Cutover())

    payload = await health_module.readiness_payload()

    assert payload["status"] == "degraded"
    assert payload["db"] == "ok"
    assert payload["runtime"]["required_services"] == {
        "worker": "ok",
        "scheduler": "missing",
    }
    assert payload["task_queue"]["queued"] == 3
    assert payload["task_queue"]["running"] == 1
    assert payload["task_queue"]["oldest_queued_age_seconds"] >= 299
