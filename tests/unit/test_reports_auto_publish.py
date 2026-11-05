from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import TaskStatus
from src.services import data_release_service as module
from src.services.data_release_service import DataReleaseService


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [("none", True), ("old", True), ("covered", False), ("failed", False), ("code_only", True), ("raw_archive_only", True)])
async def test_pending_report_reconciler_retries_without_losing_busy_events(monkeypatch, state, expected):
    now = datetime.now(timezone.utc)
    report = SimpleNamespace(completed_at=now - timedelta(minutes=2), task_uuid="report-task")
    releases = []
    if state in {"old", "covered"}:
        releases.append(SimpleNamespace(status=TaskStatus.COMPLETED, input_data={"reports_only": True},
            output_data={"r2_reports_published": True}, started_at=now - timedelta(minutes=3 if state == "old" else 1)))
    if state == "failed":
        releases.append(SimpleNamespace(status=TaskStatus.FAILED, input_data={"reports_only": True}, output_data={},
            completed_at=now - timedelta(minutes=1), created_at=now - timedelta(minutes=2)))
    if state in {"code_only", "raw_archive_only"}:
        releases.append(SimpleNamespace(status=TaskStatus.COMPLETED, input_data={state: True},
            output_data={"r2_reports_published": True}, started_at=now - timedelta(minutes=1)))
    class Result:
        def __init__(self, value=None): self.value = value
        def scalar_one_or_none(self): return self.value
        def scalars(self): return self
        def all(self): return releases
    class DB:
        def __init__(self): self.calls = 0
        async def execute(self, _query):
            self.calls += 1
            return Result(report if self.calls == 2 else None)
    @asynccontextmanager
    async def database(): yield DB()
    monkeypatch.setattr(module, "get_database", database)
    service = DataReleaseService()
    service.load_jobs = AsyncMock(return_value=[SimpleNamespace(enabled=True, job_id="site-release")])
    service._r2_reports_enabled = lambda _job: True
    service._config = lambda: SimpleNamespace(auto_failure_cooldown_minutes=30)
    service.trigger_job = AsyncMock(return_value={"status": "skipped", "reason": "release_pipeline_busy"})
    await service.reconcile_pending_reports()
    if expected:
        service.trigger_job.assert_awaited_once_with("site-release", manual=False,
            trigger="upstream_completion", trigger_task_uuid="report-task", reports_only=True)
        # A busy release does not clear the event; the next scheduler tick retries it.
        await service.reconcile_pending_reports()
        assert service.trigger_job.await_count == 2
    else:
        service.trigger_job.assert_not_awaited()
