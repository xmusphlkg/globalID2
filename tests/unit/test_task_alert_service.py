from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.domain import TaskStatus
from src.services.task_alert_service import _alert_signature, _find_recent_alert_group


def test_alert_signature_groups_download_repo_git_failures():
    first = SimpleNamespace(
        task_type="export_data",
        metadata_={"release_job_id": "site-release"},
        last_error=(
            "Generate Site Data failed with exit code 1. "
            "RuntimeError: git pull --ff-only origin master failed after 3 attempts. "
            "error: Could not read 4d22d96f6e64cac6d577545cdfbddfc716756132"
        ),
    )
    second = SimpleNamespace(
        task_type="export_data",
        metadata_={"release_job_id": "site-release"},
        last_error=(
            "Release preflight failed: Download-data repo write check failed. "
            "RuntimeError: git ls-remote --heads git@github.com:xmusphlkg/globalID2_data_download.git master failed"
        ),
    )

    assert _alert_signature(first, TaskStatus.FAILED) == _alert_signature(
        second, TaskStatus.FAILED
    )


def test_alert_signature_groups_tw_ssl_failures():
    task = SimpleNamespace(
        task_type="crawl_data",
        metadata_={},
        last_error=(
            "HTTPSConnectionPool(host='od.cdc.gov.tw', port=443): Max retries exceeded "
            "with url: /eic/Age_County_Gender_050.csv (Caused by SSLError(...))"
        ),
    )

    assert _alert_signature(task, TaskStatus.FAILED) == "failed:crawl_data:tw_nidss_ssl"


@pytest.mark.asyncio
async def test_find_recent_alert_group_returns_previous_sent_group(monkeypatch):
    signature = "failed:export_data:site-release:download_repo_git"
    current_task = SimpleNamespace(id=2)
    previous_task = SimpleNamespace(
        id=1,
        task_uuid="previous-alert",
        metadata_={
            "alert_signature": signature,
            "alert_group_sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    class FakeScalars:
        def all(self):
            return [current_task, previous_task]

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeDb:
        async def execute(self, _statement):
            return FakeResult()

    monkeypatch.setattr(
        "src.services.task_alert_service.get_config",
        lambda: SimpleNamespace(
            automation=SimpleNamespace(alert_group_cooldown_minutes=360)
        ),
    )

    grouped = await _find_recent_alert_group(
        FakeDb(),
        task=current_task,
        final_status=TaskStatus.FAILED,
        alert_signature=signature,
    )

    assert grouped is previous_task
