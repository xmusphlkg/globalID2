"""A public-series rebuild always uses the verified site-release workflow."""

import json
from unittest.mock import AsyncMock

import pytest

from scripts import backfill_public_disease_series as script


@pytest.mark.parametrize("full", [False, True])
async def test_backfill_queues_release_without_direct_projection(monkeypatch, full):
    from src.core import database
    from src.services.data_release_service import data_release_service

    trigger = AsyncMock(return_value={"task_uuid": "release-1", "status": "queued"})
    dispose = AsyncMock()
    monkeypatch.setattr(data_release_service, "trigger_job", trigger)
    monkeypatch.setattr(database, "dispose_database", dispose)
    assert await script.enqueue_release(full=full) == {
        "task_uuid": "release-1",
        "status": "queued",
    }
    trigger.assert_awaited_once_with(
        "site-release", manual=True, trigger="public-series-backfill", full=full
    )
    dispose.assert_awaited_once()


@pytest.mark.parametrize("args,full", [([], False), (["--full"], True)])
def test_backfill_cli_is_incremental_unless_explicitly_full(
    monkeypatch, capsys, args, full
):
    enqueue = AsyncMock(return_value={"task_uuid": "release-1", "status": "queued"})
    monkeypatch.setattr(script, "enqueue_release", enqueue)
    script.main(args)
    enqueue.assert_awaited_once_with(full=full)
    assert json.loads(capsys.readouterr().out)["status"] == "queued"


@pytest.mark.parametrize("args", [["--country", "US"], ["--jobs", "2"], ["--force"]])
def test_backfill_removes_direct_projection_options(args):
    with pytest.raises(SystemExit) as error:
        script.main(args)
    assert error.value.code == 2
