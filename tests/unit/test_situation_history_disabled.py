from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from dashboard.api.main import lifespan
from dashboard.api.routers import situation


@pytest.mark.asyncio
async def test_history_snapshots_short_circuit_when_history_database_disabled(monkeypatch) -> None:
    async def disabled_health() -> dict:
        return {"enabled": False, "status": "disabled"}

    def fail_history_db():
        raise AssertionError("history database should not be opened")

    monkeypatch.setattr(situation, "history_health", disabled_health)
    monkeypatch.setattr(situation, "get_history_db", fail_history_db)

    with pytest.raises(HTTPException) as exc:
        await situation.history_snapshots(Response())

    assert exc.value.status_code == 503
    assert exc.value.detail == "Situation history database is disabled"


@pytest.mark.asyncio
async def test_api_lifespan_disposes_history_database(monkeypatch) -> None:
    calls: list[str] = []

    class _RuntimeRegistry:
        def new_instance_id(self, service: str) -> str:
            return f"{service}-test"

        async def run_heartbeat(self, service: str, instance_id: str, stop_event) -> None:
            await stop_event.wait()

        async def close(self) -> None:
            calls.append("runtime")

    class _Events:
        async def publish(self, *_args, **_kwargs) -> None:
            return None

        async def publish_task_event(self, *_args, **_kwargs) -> None:
            return None

        async def close(self) -> None:
            calls.append("events")

    class _TaskManager:
        def set_broadcast_hook(self, _hook) -> None:
            return None

    monkeypatch.setattr("dashboard.api.main.get_engine", lambda: None)
    monkeypatch.setattr("dashboard.api.main.runtime_registry", _RuntimeRegistry())
    monkeypatch.setattr("dashboard.api.main.control_plane_events", _Events())
    monkeypatch.setattr("dashboard.api.main.task_manager", _TaskManager())

    async def dispose_history() -> None:
        calls.append("history")

    async def dispose_primary() -> None:
        calls.append("primary")

    monkeypatch.setattr("dashboard.api.main.dispose_history_database", dispose_history)
    monkeypatch.setattr("dashboard.api.main.dispose_database", dispose_primary)

    async with lifespan(object()):
        pass

    assert calls[-2:] == ["history", "primary"]
