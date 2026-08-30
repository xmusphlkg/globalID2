from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard.api.schemas.sources import AutomationConfigOut
from src.data.processors.cn import DataProcessor
from src.domain import TaskStatus, TaskType
from src.services import automation_service as automation_module
from src.services import crawl_service as crawl_module
from src.services import crawl_task_service as crawl_task_module
from src.services import _lifecycle as lifecycle_module
from src.services import task_executor as task_executor_module
from src.services.automation_service import (
    AutomationJobConfig,
    AutomationService,
    _ingestion_job_health,
)
from src.services.crawl_service import CrawlService
from src.services.ingestion_resilience import (
    automatic_ingestion_trigger_eligible,
    classify_ingestion_failure,
)


def test_classifier_retries_transport_and_impossible_empty_payloads_only():
    tls = classify_ingestion_failure(
        RuntimeError(
            "HTTPSConnectionPool: Max retries exceeded; "
            "SSLError unexpected EOF while reading"
        )
    )
    assert tls.retryable is True
    assert tls.category == "transient_transport"

    empty = classify_ingestion_failure(
        RuntimeError("THL cube returned no national monthly Cases rows")
    )
    assert empty.retryable is True
    assert empty.category == "transient_upstream_empty"

    unknown = classify_ingestion_failure(RuntimeError("something unusual happened"))
    assert unknown.retryable is False
    assert unknown.category == "unclassified"


@pytest.mark.parametrize(
    "message",
    [
        "PHO IDTO monthly table discovery returned 0 matches",
        "Disease series observation quality gate failed",
        "SurvStat WebForms control contract changed",
        "AGES CSV must contain exactly one non-cumulative month column",
        "HTTP 403 Forbidden",
        "Unsupported country: XY",
        "value too long for type character varying(1000)",
        "Conflicting source rows share a disease series observation identity",
        "THL CSV row has unknown time member 'All years'",
    ],
)
def test_classifier_keeps_contract_quality_and_credentials_terminal(message):
    result = classify_ingestion_failure(RuntimeError(message))
    assert result.retryable is False
    assert result.category == "permanent"


def test_only_unattended_scheduler_tasks_are_retry_eligible():
    assert automatic_ingestion_trigger_eligible(
        {
            "automation_job_id": "hk-daily",
            "scheduled_trigger": True,
            "manual_trigger": False,
        }
    )
    assert not automatic_ingestion_trigger_eligible(
        {
            "automation_job_id": "hk-daily",
            "scheduled_trigger": False,
            "manual_trigger": True,
        }
    )
    assert not automatic_ingestion_trigger_eligible(
        {"scheduled_trigger": True, "manual_trigger": False}
    )


def test_job_health_distinguishes_recovery_failure_and_staleness():
    now = datetime.now(timezone.utc)
    job = AutomationJobConfig(
        job_id="hk-daily",
        name="HK daily",
        country_code="HK",
        interval_minutes=60,
    )
    success = SimpleNamespace(
        status=TaskStatus.COMPLETED,
        completed_at=now - timedelta(minutes=10),
    )
    assert _ingestion_job_health(job, success, success, now=now)["health_status"] == "healthy"

    retrying = SimpleNamespace(status=TaskStatus.RETRYING, completed_at=None)
    assert _ingestion_job_health(job, retrying, success, now=now)["health_status"] == "recovering"

    failed = SimpleNamespace(status=TaskStatus.FAILED, completed_at=now)
    assert _ingestion_job_health(job, failed, success, now=now)["health_status"] == "failed"

    stale_success = SimpleNamespace(
        status=TaskStatus.COMPLETED,
        completed_at=now - timedelta(minutes=121),
    )
    health = _ingestion_job_health(job, stale_success, stale_success, now=now)
    assert health["health_status"] == "stale"
    assert health["stale_after_minutes"] == 120


def test_dashboard_contract_preserves_ingestion_health_and_retry_state():
    payload = AutomationConfigOut(
        enabled=True,
        timezone="UTC",
        poll_interval_seconds=30,
        default_retry_threshold=3,
        jobs=[
            {
                "job_id": "hk-daily",
                "name": "HK daily",
                "country_code": "HK",
                "source": "chp_notifiable",
                "enabled": True,
                "priority": "normal",
                "process": True,
                "save_raw": True,
                "fill_missing": False,
                "force": False,
                "include_current_month": False,
                "revision_window_months": 3,
                "retry_threshold": 3,
                "last_status": "retrying",
                "health_status": "recovering",
                "health_reason": "a bounded automatic retry is scheduled",
                "last_success_at": "2026-08-16T00:00:00+00:00",
                "last_success_age_minutes": 60,
                "stale_after_minutes": 2880,
                "automatic_retry": {"status": "scheduled", "scheduled_attempts": 1},
            }
        ],
    ).model_dump()

    job = payload["jobs"][0]
    assert job["health_status"] == "recovering"
    assert job["automatic_retry"]["status"] == "scheduled"


class _ScalarResult:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._many


class _Database:
    def __init__(self, results):
        self.results = list(results)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query):
        return self.results.pop(0)

    async def commit(self):
        self.commits += 1


def _retry_config():
    return SimpleNamespace(
        default_retry_threshold=3,
        auto_retry_base_delay_seconds=60,
        auto_retry_max_delay_seconds=600,
    )


@pytest.mark.asyncio
async def test_transient_scheduled_ingestion_failure_is_parked_for_retry(
    monkeypatch,
):
    task = SimpleNamespace(
        task_uuid="crawl-1",
        task_type=TaskType.CRAWL_DATA,
        status=TaskStatus.FAILED,
        retry_count=1,
        input_data={
            "automation_job_id": "hk-daily",
            "scheduled_trigger": True,
            "manual_trigger": False,
            "retry_threshold": 3,
        },
        metadata_={},
    )
    database = _Database([_ScalarResult(one=task)])
    service = AutomationService()
    entries = []
    monkeypatch.setattr(automation_module, "get_database", lambda: database)
    monkeypatch.setattr(service, "_config", _retry_config)

    async def add_entry(*_args, **kwargs):
        entries.append(kwargs)

    monkeypatch.setattr(automation_module.task_manager, "add_workbook_entry", add_entry)

    scheduled = await service.schedule_automatic_retry_after_failure(
        task.task_uuid,
        RuntimeError("connection reset by peer"),
    )

    assert scheduled is True
    assert task.status == TaskStatus.RETRYING
    retry = task.metadata_["ingestion_automatic_retry"]
    assert retry["status"] == "scheduled"
    assert retry["scheduled_attempts"] == 1
    assert retry["max_scheduled_attempts"] == 2
    assert retry["delay_seconds"] == 60
    assert datetime.fromisoformat(retry["next_attempt_at"]) > datetime.now(timezone.utc)
    assert entries[0]["metadata"]["event"] == "ingestion_auto_retry_scheduled"


@pytest.mark.asyncio
async def test_contract_drift_and_manual_runs_remain_terminal(monkeypatch):
    service = AutomationService()
    monkeypatch.setattr(service, "_config", _retry_config)

    contract_task = SimpleNamespace(
        task_uuid="crawl-contract",
        task_type=TaskType.CRAWL_DATA,
        status=TaskStatus.FAILED,
        retry_count=1,
        input_data={
            "automation_job_id": "ca-on-daily",
            "scheduled_trigger": True,
            "manual_trigger": False,
            "retry_threshold": 3,
        },
        metadata_={},
    )
    monkeypatch.setattr(
        automation_module,
        "get_database",
        lambda: _Database([_ScalarResult(one=contract_task)]),
    )
    assert not await service.schedule_automatic_retry_after_failure(
        contract_task.task_uuid,
        RuntimeError("PHO IDTO monthly table discovery returned 0 matches"),
    )
    assert contract_task.status == TaskStatus.FAILED
    assert contract_task.metadata_["ingestion_automatic_retry"]["category"] == "permanent"

    manual_task = SimpleNamespace(
        task_uuid="crawl-manual",
        task_type=TaskType.CRAWL_DATA,
        status=TaskStatus.FAILED,
        retry_count=1,
        input_data={
            "automation_job_id": "hk-daily",
            "scheduled_trigger": False,
            "manual_trigger": True,
            "retry_threshold": 3,
        },
        metadata_={},
    )
    monkeypatch.setattr(
        automation_module,
        "get_database",
        lambda: _Database([_ScalarResult(one=manual_task)]),
    )
    assert not await service.schedule_automatic_retry_after_failure(
        manual_task.task_uuid,
        RuntimeError("connection reset by peer"),
    )
    assert manual_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_due_ingestion_retry_is_requeued_without_competing_country_task(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    due = SimpleNamespace(
        id=1,
        task_uuid="due-crawl",
        task_type=TaskType.CRAWL_DATA,
        status=TaskStatus.RETRYING,
        country_id=9,
        progress=80,
        completed_steps=2,
        started_at=now - timedelta(minutes=5),
        completed_at=now - timedelta(minutes=1),
        actual_duration=240,
        created_at=now - timedelta(minutes=10),
        input_data={
            "automation_job_id": "hk-daily",
            "scheduled_trigger": True,
            "manual_trigger": False,
        },
        metadata_={
            "ingestion_automatic_retry": {
                "status": "scheduled",
                "scheduled_attempts": 1,
                "max_scheduled_attempts": 2,
                "next_attempt_at": (now - timedelta(seconds=1)).isoformat(),
            }
        },
    )
    database = _Database(
        [_ScalarResult(many=[due]), _ScalarResult(one=None)]
    )
    entries = []
    broadcasts = []
    service = AutomationService()
    monkeypatch.setattr(automation_module, "get_database", lambda: database)

    async def add_entry(*_args, **kwargs):
        entries.append(kwargs)

    async def broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(automation_module.task_manager, "add_workbook_entry", add_entry)
    monkeypatch.setattr(automation_module.task_manager, "_broadcast", broadcast)

    assert await service.requeue_due_automatic_retries(now=now) == ["due-crawl"]
    assert due.status == TaskStatus.QUEUED
    assert due.progress == 0
    assert due.metadata_["ingestion_automatic_retry"]["status"] == "queued"
    assert entries[0]["metadata"]["event"] == "ingestion_auto_retry_queued"
    assert broadcasts[0]["automatic_retry"] is True


@pytest.mark.asyncio
async def test_parked_retry_blocks_duplicate_country_enqueue(monkeypatch):
    country = SimpleNamespace(id=7, code="HK")
    retrying = SimpleNamespace(
        task_uuid="existing-retry",
        status=TaskStatus.RETRYING,
    )
    database = _Database(
        [_ScalarResult(one=country), _ScalarResult(one=retrying)]
    )
    monkeypatch.setattr(crawl_task_module, "get_database", lambda: database)

    result = await crawl_task_module.CrawlTaskService().enqueue_crawl_task(
        country_code="HK",
        source="chp_notifiable",
    )

    assert result.created is False
    assert result.task is retrying
    assert result.skipped_reason == "already_running"


@pytest.mark.asyncio
async def test_failed_pipeline_finalizes_its_current_crawl_audit_row(monkeypatch):
    started = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=42,
        status="running",
        finished_at=None,
        error_message=None,
        metadata_={"process": True},
    )
    database = _Database([_ScalarResult(one=run)])
    monkeypatch.setattr(crawl_module, "get_database", lambda: database)

    run_id = await CrawlService().fail_current_run(
        country_code="hk",
        source="CHP_NOTIFIABLE",
        started_after=started,
        error=RuntimeError("connection reset by peer"),
    )

    assert run_id == 42
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.error_message == "connection reset by peer"
    assert run.metadata_["failed_closed"] is True
    assert database.commits == 1


@pytest.mark.asyncio
async def test_worker_restart_closes_interrupted_crawl_run(monkeypatch):
    started = datetime.now(timezone.utc) - timedelta(minutes=3)
    task = SimpleNamespace(
        id=11,
        task_uuid="interrupted-crawl",
        task_type=TaskType.CRAWL_DATA,
        status=TaskStatus.RUNNING,
        started_at=started,
        completed_at=None,
        actual_duration=None,
        last_error=None,
        report_id=None,
        output_data={},
        progress=40,
        input_data={
            "country": "HK",
            "source": "chp_notifiable",
        },
    )
    database = _Database([_ScalarResult(many=[task])])
    finalized = []
    monkeypatch.setattr(task_executor_module, "get_database", lambda: database)

    async def finalize(_self, **kwargs):
        finalized.append(kwargs)
        return 77

    async def no_workbook(*_args, **_kwargs):
        return None

    async def no_broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(CrawlService, "fail_current_run", finalize)
    monkeypatch.setattr(task_executor_module.task_manager, "add_workbook_entry", no_workbook)
    monkeypatch.setattr(task_executor_module.task_manager, "_broadcast", no_broadcast)

    assert await task_executor_module.recover_interrupted_tasks_on_startup() == 1
    assert task.status == TaskStatus.CANCELLED
    assert finalized[0]["country_code"] == "HK"
    assert finalized[0]["source"] == "chp_notifiable"
    assert finalized[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_stale_literature_task_is_boundedly_requeued_but_fresh_task_is_untouched(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    stale = SimpleNamespace(
        id=21,
        task_uuid="stale-literature-sync",
        task_type=TaskType.SYNC_LITERATURE,
        status=TaskStatus.RUNNING,
        started_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
        completed_at=None,
        actual_duration=None,
        last_error=None,
        retry_count=0,
        max_retries=2,
        report_id=None,
        output_data={},
        progress=55,
        input_data={},
        metadata_={
            "task_lease": {
                "owner": "dead-worker",
                "heartbeat_at": (now - timedelta(minutes=10)).isoformat(),
            }
        },
    )
    fresh = SimpleNamespace(
        id=22,
        task_uuid="fresh-literature-sync",
        task_type=TaskType.SYNC_LITERATURE,
        status=TaskStatus.RUNNING,
        started_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(seconds=5),
        created_at=now - timedelta(minutes=1),
        completed_at=None,
        actual_duration=None,
        last_error=None,
        retry_count=0,
        max_retries=2,
        report_id=None,
        output_data={},
        progress=10,
        input_data={},
        metadata_={
            "task_lease": {
                "owner": "live-worker",
                "heartbeat_at": (now - timedelta(seconds=5)).isoformat(),
            }
        },
    )
    database = _Database([
        _ScalarResult(many=[stale, fresh]),
        _ScalarResult(many=[]),
    ])
    entries = []
    broadcasts = []
    monkeypatch.setattr(task_executor_module, "get_database", lambda: database)

    async def add_entry(*args, **kwargs):
        entries.append((args, kwargs))

    async def broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(task_executor_module.task_manager, "add_workbook_entry", add_entry)
    monkeypatch.setattr(task_executor_module.task_manager, "_broadcast", broadcast)

    recovered = await task_executor_module.recover_interrupted_tasks_on_startup(
        stale_after_seconds=180,
        now=now,
    )

    assert recovered == 1
    assert stale.status == TaskStatus.QUEUED
    assert stale.retry_count == 1
    assert stale.started_at is None
    assert stale.metadata_["task_recovery_history"][0]["previous_owner"] == "dead-worker"
    assert fresh.status == TaskStatus.RUNNING
    assert len(entries) == 1
    assert broadcasts[0]["status"] == TaskStatus.QUEUED.value


@pytest.mark.asyncio
async def test_stale_recovery_can_be_restricted_to_one_dead_owner(monkeypatch):
    now = datetime.now(timezone.utc)

    def task(task_uuid, owner):
        return SimpleNamespace(
            id=30 if owner == "dead-a" else 31,
            task_uuid=task_uuid,
            task_type=TaskType.SYNC_LITERATURE,
            status=TaskStatus.RUNNING,
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=10),
            completed_at=None,
            actual_duration=None,
            last_error=None,
            retry_count=0,
            max_retries=2,
            report_id=None,
            output_data={},
            progress=50,
            input_data={},
            metadata_={"task_lease": {
                "owner": owner,
                "heartbeat_at": (now - timedelta(minutes=10)).isoformat(),
            }},
        )

    owned = task("owned-stale", "dead-a")
    unrelated = task("unrelated-stale", "dead-b")
    database = _Database([
        _ScalarResult(many=[owned, unrelated]),
        _ScalarResult(many=[]),
    ])
    monkeypatch.setattr(task_executor_module, "get_database", lambda: database)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(task_executor_module.task_manager, "add_workbook_entry", noop)
    monkeypatch.setattr(task_executor_module.task_manager, "_broadcast", noop)

    recovered = await task_executor_module.recover_interrupted_tasks_on_startup(
        stale_after_seconds=0,
        now=now,
        only_owner="dead-a",
    )

    assert recovered == 1
    assert owned.status == TaskStatus.QUEUED
    assert unrelated.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_stale_literature_task_fails_after_recovery_limit(monkeypatch):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        id=23,
        task_uuid="exhausted-literature-sync",
        task_type=TaskType.ENRICH_LITERATURE,
        status=TaskStatus.RUNNING,
        started_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
        completed_at=None,
        actual_duration=None,
        last_error=None,
        retry_count=2,
        max_retries=2,
        report_id=None,
        output_data={},
        progress=70,
        input_data={},
        metadata_={"task_lease": {"owner": "dead-worker"}},
    )
    database = _Database([_ScalarResult(many=[task])])
    monkeypatch.setattr(task_executor_module, "get_database", lambda: database)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(task_executor_module.task_manager, "add_workbook_entry", noop)
    monkeypatch.setattr(task_executor_module.task_manager, "_broadcast", noop)

    assert (
        await task_executor_module.recover_interrupted_tasks_on_startup(
            stale_after_seconds=180,
            now=now,
        )
        == 1
    )
    assert task.status == TaskStatus.FAILED
    assert task.completed_at == now
    assert "limit exhausted" in task.last_error


@pytest.mark.asyncio
async def test_lifecycle_suppresses_alert_when_ingestion_retry_is_scheduled(
    monkeypatch,
):
    task = SimpleNamespace(
        task_uuid="crawl-lifecycle",
        task_type=TaskType.CRAWL_DATA,
    )
    statuses = []
    alerts = []

    async def update_status(_task_uuid, status, **_kwargs):
        statuses.append(status)

    async def add_entry(*_args, **_kwargs):
        return None

    async def schedule_retry(_task_uuid, _exc):
        return True

    async def send_alert(*args):
        alerts.append(args)

    monkeypatch.setattr(lifecycle_module.task_manager, "update_task_status", update_status)
    monkeypatch.setattr(lifecycle_module.task_manager, "add_workbook_entry", add_entry)
    monkeypatch.setattr(
        lifecycle_module.automation_service,
        "schedule_automatic_retry_after_failure",
        schedule_retry,
    )
    monkeypatch.setattr(lifecycle_module.task_alert_service, "send_task_alert", send_alert)

    with pytest.raises(RuntimeError, match="gateway timeout"):
        async with lifecycle_module.task_lifecycle(task, exit_on_cancel=False):
            raise RuntimeError("gateway timeout")

    assert statuses == [TaskStatus.RUNNING, TaskStatus.FAILED]
    assert alerts == []


@pytest.mark.asyncio
async def test_cn_processing_fails_closed_when_every_selected_report_fails(
    tmp_path,
    monkeypatch,
):
    processor = DataProcessor(output_dir=tmp_path, country_code="CN")

    async def fail_one(*_args, **_kwargs):
        raise ValueError("official table columns changed")

    monkeypatch.setattr(processor, "_process_single_result", fail_one)

    with pytest.raises(RuntimeError, match="no usable datasets from 2 selected report"):
        await processor.process_crawler_results(
            [SimpleNamespace(), SimpleNamespace()],
            save_to_file=False,
        )


@pytest.mark.asyncio
async def test_cn_processing_keeps_partial_success_but_does_not_hide_all_failure(
    tmp_path,
    monkeypatch,
):
    processor = DataProcessor(output_dir=tmp_path, country_code="CN")
    frame = pd.DataFrame([{"Diseases": "Test", "Cases": 1}])
    calls = 0

    async def mixed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return frame
        raise ValueError("one old report format is unsupported")

    monkeypatch.setattr(processor, "_process_single_result", mixed)

    result = await processor.process_crawler_results(
        [SimpleNamespace(), SimpleNamespace()],
        save_to_file=False,
    )
    assert result == [frame]
