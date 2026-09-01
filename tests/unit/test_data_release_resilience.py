from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.domain import TaskStatus, TaskType
from src.services import data_release_service as release_module
from src.services import _lifecycle as lifecycle_module
from src.services.data_release.pipeline import ReleasePreflightError
from src.services.data_release.process_runner import ReleaseCommandError
from src.services.data_release.resilience import (
    automatic_trigger_eligible,
    classify_release_failure,
)
from src.services.data_release_service import DataReleaseService


def _command_error(
    message: str,
    *,
    stage: str,
    timed_out: bool = False,
) -> ReleaseCommandError:
    return ReleaseCommandError(
        message,
        release_stage=stage,
        title="release command",
        returncode=1,
        timed_out=timed_out,
    )


def test_classifier_retries_only_allowlisted_transient_external_failures():
    transient = classify_release_failure(
        _command_error(
            "TypeError: fetch failed because connection reset by peer",
            stage="cloudflare_pages_deploy",
        )
    )
    # A code signature wins over a transport-looking suffix.
    assert transient.retryable is False
    assert transient.category == "permanent"

    transient = classify_release_failure(
        _command_error(
            "fetch failed: connection reset by peer",
            stage="cloudflare_pages_deploy",
        )
    )
    assert transient.retryable is True
    assert transient.stage == "cloudflare_pages_deploy"

    dispatch = classify_release_failure(
        _command_error(
            "worker_transport_error:URLError",
            stage="situation_alert_dispatch",
        )
    )
    assert dispatch.retryable is True
    assert dispatch.stage == "situation_alert_dispatch"

    build_timeout = classify_release_failure(
        _command_error(
            "Astro build timed out",
            stage="astro_build",
            timed_out=True,
        )
    )
    assert build_timeout.retryable is False


def test_classifier_keeps_preflight_syntax_and_credentials_terminal():
    syntax = classify_release_failure(
        ReleasePreflightError(
            {
                "blockers": ["Situation command unavailable"],
                "commands": {"error": "SyntaxError: invalid syntax"},
            }
        )
    )
    assert syntax.retryable is False

    credential = classify_release_failure(
        _command_error(
            "Authentication failed: HTTP 403 Forbidden",
            stage="cloudflare_pages_deploy",
        )
    )
    assert credential.retryable is False


def test_classifier_allows_transient_cloudflare_preflight_diagnostic():
    failure = ReleasePreflightError(
        {
            "blockers": ["Cloudflare Pages project check failed."],
            "cloudflare": {"error": "fetch failed: gateway timeout"},
        }
    )
    result = classify_release_failure(failure)
    assert result.retryable is True
    assert result.stage == "release_preflight"


def test_classifier_allows_openssl_eof_preflight_diagnostic():
    failure = ReleasePreflightError(
        {
            "blockers": ["Cloudflare Pages project check failed."],
            "cloudflare": {
                "error": (
                    "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] "
                    "EOF occurred in violation of protocol>"
                )
            },
        }
    )
    result = classify_release_failure(failure)
    assert result.retryable is True
    assert result.category == "transient_external"
    assert result.stage == "release_preflight"


def test_classifier_keeps_missing_cloudflare_production_branch_terminal():
    failure = ReleasePreflightError(
        {
            "blockers": [
                "Cloudflare Pages project check failed.",
                "Cloudflare Pages project has no production branch configured.",
            ],
            "cloudflare": {
                "error": (
                    "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] "
                    "EOF occurred in violation of protocol>"
                )
            },
        }
    )
    result = classify_release_failure(failure)
    assert result.retryable is False
    assert result.category == "permanent"
    assert result.stage == "release_preflight"


def test_only_unattended_release_triggers_are_retry_eligible():
    assert automatic_trigger_eligible(
        {"trigger": "scheduled", "manual_trigger": False}
    )
    assert automatic_trigger_eligible(
        {"trigger": "upstream_completion", "manual_trigger": False}
    )
    assert not automatic_trigger_eligible(
        {"trigger": "manual", "manual_trigger": True}
    )
    assert not automatic_trigger_eligible(
        {"trigger": "scheduled", "manual_trigger": True}
    )


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


@pytest.mark.asyncio
async def test_transient_automatic_failure_is_persisted_as_delayed_retry(
    monkeypatch,
):
    task = SimpleNamespace(
        task_uuid="release-1",
        task_type=TaskType.EXPORT_DATA,
        status=TaskStatus.FAILED,
        input_data={"trigger": "scheduled", "manual_trigger": False},
        metadata_={"release_job_id": "site-release"},
    )
    database = _Database([_ScalarResult(one=task)])
    entries = []
    service = DataReleaseService()
    monkeypatch.setattr(release_module, "get_database", lambda: database)
    monkeypatch.setattr(
        service,
        "_config",
        lambda: SimpleNamespace(
            auto_retry_max_attempts=3,
            auto_retry_base_delay_seconds=60,
            auto_retry_max_delay_seconds=600,
        ),
    )

    async def add_entry(*_args, **kwargs):
        entries.append(kwargs)

    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_entry)

    scheduled = await service.schedule_automatic_retry_after_failure(
        task.task_uuid,
        _command_error(
            "fetch failed: connection reset",
            stage="cloudflare_pages_deploy",
        ),
    )

    assert scheduled is True
    assert task.status == TaskStatus.RETRYING
    retry = task.metadata_["automatic_retry"]
    assert retry["status"] == "scheduled"
    assert retry["scheduled_attempts"] == 1
    assert retry["delay_seconds"] == 60
    assert datetime.fromisoformat(retry["next_attempt_at"]) > datetime.now(timezone.utc)
    assert entries[0]["metadata"]["event"] == "release_auto_retry_scheduled"


@pytest.mark.asyncio
async def test_retry_cap_and_manual_trigger_remain_terminal(monkeypatch):
    service = DataReleaseService()
    monkeypatch.setattr(
        service,
        "_config",
        lambda: SimpleNamespace(
            auto_retry_max_attempts=2,
            auto_retry_base_delay_seconds=60,
            auto_retry_max_delay_seconds=600,
        ),
    )
    failure = _command_error(
        "fetch failed: service unavailable",
        stage="cloudflare_pages_deploy",
    )

    exhausted = SimpleNamespace(
        task_uuid="release-exhausted",
        task_type=TaskType.EXPORT_DATA,
        status=TaskStatus.FAILED,
        input_data={"trigger": "upstream_completion", "manual_trigger": False},
        metadata_={
            "release_job_id": "site-release",
            "automatic_retry": {"scheduled_attempts": 2},
        },
    )
    database = _Database([_ScalarResult(one=exhausted)])
    monkeypatch.setattr(release_module, "get_database", lambda: database)
    assert not await service.schedule_automatic_retry_after_failure(
        exhausted.task_uuid, failure
    )
    assert exhausted.status == TaskStatus.FAILED
    assert exhausted.metadata_["automatic_retry"]["exhausted"] is True

    manual = SimpleNamespace(
        task_uuid="release-manual",
        task_type=TaskType.EXPORT_DATA,
        status=TaskStatus.FAILED,
        input_data={"trigger": "manual", "manual_trigger": True},
        metadata_={"release_job_id": "site-release"},
    )
    database = _Database([_ScalarResult(one=manual)])
    monkeypatch.setattr(release_module, "get_database", lambda: database)
    assert not await service.schedule_automatic_retry_after_failure(
        manual.task_uuid, failure
    )
    assert manual.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_due_retry_is_atomically_requeued_and_future_retry_stays_parked(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    due = SimpleNamespace(
        id=1,
        task_uuid="due-release",
        task_type=TaskType.EXPORT_DATA,
        status=TaskStatus.RETRYING,
        progress=70,
        completed_steps=3,
        started_at=now - timedelta(minutes=5),
        completed_at=now - timedelta(minutes=1),
        actual_duration=240,
        last_error="gateway timeout",
        created_at=now - timedelta(minutes=10),
        input_data={"trigger": "scheduled", "manual_trigger": False},
        metadata_={
            "release_job_id": "site-release",
            "automatic_retry": {
                "status": "scheduled",
                "scheduled_attempts": 1,
                "max_attempts": 3,
                "next_attempt_at": (now - timedelta(seconds=1)).isoformat(),
            },
        },
    )
    future = SimpleNamespace(
        **{
            **due.__dict__,
            "id": 2,
            "task_uuid": "future-release",
            "metadata_": {
                "release_job_id": "other-release",
                "automatic_retry": {
                    "status": "scheduled",
                    "scheduled_attempts": 1,
                    "max_attempts": 3,
                    "next_attempt_at": (now + timedelta(minutes=5)).isoformat(),
                },
            },
        }
    )
    database = _Database(
        [
            _ScalarResult(many=[due, future]),
            _ScalarResult(many=[]),
        ]
    )
    service = DataReleaseService()
    entries = []
    broadcasts = []
    monkeypatch.setattr(release_module, "get_database", lambda: database)

    async def add_entry(*_args, **kwargs):
        entries.append(kwargs)

    async def broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(release_module.task_manager, "add_workbook_entry", add_entry)
    monkeypatch.setattr(release_module.task_manager, "_broadcast", broadcast)

    result = await service.requeue_due_automatic_retries(now=now)

    assert result == ["due-release"]
    assert due.status == TaskStatus.QUEUED
    assert due.progress == 0
    assert due.metadata_["automatic_retry"]["status"] == "queued"
    assert future.status == TaskStatus.RETRYING
    assert entries[0]["metadata"]["event"] == "release_auto_retry_queued"
    assert broadcasts[0]["automatic_retry"] is True


@pytest.mark.asyncio
async def test_lifecycle_suppresses_operator_alert_while_automatic_retry_is_scheduled(
    monkeypatch,
):
    task = SimpleNamespace(
        task_uuid="release-lifecycle",
        task_type=TaskType.EXPORT_DATA,
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
        lifecycle_module.data_release_service,
        "schedule_automatic_retry_after_failure",
        schedule_retry,
    )
    monkeypatch.setattr(lifecycle_module.task_alert_service, "send_task_alert", send_alert)

    with pytest.raises(RuntimeError, match="gateway timeout"):
        async with lifecycle_module.task_lifecycle(task, exit_on_cancel=False):
            raise RuntimeError("gateway timeout")

    assert statuses == [TaskStatus.RUNNING, TaskStatus.FAILED]
    assert alerts == []
