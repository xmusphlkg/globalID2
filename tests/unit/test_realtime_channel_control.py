from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import TaskStatus
from src.services import data_release_service as release_module
from src.services.data_release.channel_control import ChannelControlStore
from src.services.data_release_service import DataReleaseJobConfig, DataReleaseService


def _pause_in_process(path, channel, ready, start):
    ready.set()
    if not start.wait(10):
        raise RuntimeError("control test timed out")
    ChannelControlStore(Path(path)).set_paused(channel, True)


def test_missing_control_state_is_read_only_and_defaults_to_automatic(tmp_path):
    path = tmp_path / "controls.json"
    state = ChannelControlStore(path).snapshot()
    assert all(not item["paused"] and item["error_code"] is None for item in state.values())
    assert not path.exists()
    assert not list(tmp_path.iterdir())


def test_literature_pause_uses_an_independent_state_file(tmp_path):
    path = tmp_path / 'controls.json'
    path.write_text(json.dumps({'schema_version': 1, 'channels': {
        'surveillance': {'paused': True, 'updated_at': None},
        'situation': {'paused': False, 'updated_at': None}}}))
    state = ChannelControlStore(path).snapshot()
    assert state['surveillance']['paused'] and not state['situation']['paused']
    literature = ChannelControlStore(tmp_path / 'literature.json', channels=('literature',))
    literature.set_paused('literature', True)
    assert literature.snapshot()['literature']['paused']
    assert ChannelControlStore(path).snapshot() == state


def test_unified_controls_keep_literature_and_observation_pause_files_separate(tmp_path, monkeypatch):
    from src.services.data_release import channel_control
    monkeypatch.setattr(channel_control, "get_config", lambda: SimpleNamespace(data_dir=tmp_path))
    state = channel_control.get_publication_controls()
    assert set(state) == {"surveillance", "situation", "literature"}
    ChannelControlStore(tmp_path / "literature-publishing-control.json", channels=("literature",)).set_paused("literature", True)
    state = channel_control.get_publication_controls()
    assert state["literature"]["paused"]
    assert not state["surveillance"]["paused"] and not state["situation"]["paused"]


def test_pause_is_durable_isolated_and_private(tmp_path):
    path = tmp_path / "controls.json"
    first = ChannelControlStore(path)
    value = first.set_paused("situation", True)
    assert value["paused"] is True
    assert datetime.fromisoformat(value["updated_at"]).tzinfo is not None
    second = ChannelControlStore(path)
    assert second.snapshot()["situation"] == value
    assert not second.snapshot()["surveillance"]["paused"]
    second.set_paused("surveillance", True)
    second.set_paused("situation", False)
    assert first.snapshot()["surveillance"]["paused"]
    assert not first.snapshot()["situation"]["paused"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".realtime-control-*"))


@pytest.mark.parametrize("payload", [
    "not json", "[]", '{"schema_version":1,"channels":{}}',
    json.dumps({"schema_version": 2, "channels": {}}),
    json.dumps({"schema_version": True, "channels": {}}),
    json.dumps({"schema_version": 1, "channels": {
        "surveillance": {"paused": "false"}, "situation": {"paused": False}}}),
    pytest.param(" " * (64 * 1024 + 1), id="oversized-state"),
])
def test_invalid_state_fails_closed_without_mutation(tmp_path, payload):
    path = tmp_path / "controls.json"
    path.write_text(payload)
    state = ChannelControlStore(path).snapshot()
    assert all(item["paused"] and item["error_code"] == "control_state_invalid" for item in state.values())
    assert path.read_text() == payload


def test_explicit_recovery_does_not_unpause_the_other_channel(tmp_path):
    path = tmp_path / "controls.json"
    path.write_text("broken")
    store = ChannelControlStore(path)
    store.set_paused("situation", False)
    assert not store.snapshot()["situation"]["paused"]
    assert store.snapshot()["surveillance"]["paused"]
    assert all(item["error_code"] is None for item in store.snapshot().values())


def test_concurrent_processes_preserve_both_channel_updates(tmp_path):
    path = tmp_path / "controls.json"
    context = multiprocessing.get_context("spawn")
    ready = [context.Event(), context.Event()]
    start = context.Event()
    processes = [context.Process(target=_pause_in_process, args=(
        str(path), channel, ready[index], start))
        for index, channel in enumerate(("situation", "surveillance"))]
    try:
        for process in processes:
            process.start()
        assert all(event.wait(15) for event in ready)
        start.set()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert all(item["paused"] for item in ChannelControlStore(path).snapshot().values())
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


@pytest.mark.parametrize("channel,paused", [("other", True), ("situation", 1)])
def test_invalid_updates_do_not_create_a_file(tmp_path, channel, paused):
    path = tmp_path / "controls.json"
    with pytest.raises(ValueError):
        ChannelControlStore(path).set_paused(channel, paused)
    assert not path.exists()


@pytest.fixture
def service(monkeypatch):
    value = DataReleaseService()
    monkeypatch.setattr(value, "_config", lambda: SimpleNamespace(
        surveillance_json_enabled=True, situation_json_enabled=True))
    monkeypatch.setattr(release_module, "is_automatic_json_paused", lambda line: line == "situation")
    value.load_jobs = AsyncMock(return_value=[DataReleaseJobConfig(job_id="site", name="Site")])
    value._enqueue_release_task = AsyncMock(return_value={"status": "queued", "task_uuid": "json-run"})
    return value


@pytest.mark.asyncio
async def test_pause_blocks_new_automatic_runs_but_manual_and_other_channel_work(service):
    result = await service.trigger_job("site", manual=False, trigger="upstream_completion",
                                       data_only=True, data_line="situation")
    assert result["reason"] == "json_channel_paused"
    service._enqueue_release_task.assert_not_awaited()
    for manual, channel in ((True, "situation"), (False, "surveillance")):
        result = await service.trigger_job("site", manual=manual, trigger="dashboard",
                                           data_only=True, data_line=channel)
        assert result["task_uuid"] == "json-run"
    assert service._enqueue_release_task.await_count == 2
    assert service._states == {}


@pytest.mark.asyncio
async def test_paused_reconcile_does_not_select_or_modify_pending_markers(service):
    await service.reconcile_pending_situation()
    service.load_jobs.assert_not_awaited()
    service._enqueue_release_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotation_never_yields_to_a_paused_channel(service):
    assert not await service._yield_json_turn("site", "surveillance")


@pytest.mark.asyncio
async def test_paused_due_retry_keeps_its_durable_reservation(service, monkeypatch):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(status=TaskStatus.RETRYING,
        input_data={"data_only": True, "data_line": "situation"},
        metadata_={"automatic_retry": {"status": "scheduled",
            "next_attempt_at": (now - timedelta(minutes=5)).isoformat()}})
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [task]))), commit=AsyncMock())
    @asynccontextmanager
    async def database():
        yield db
    monkeypatch.setattr(release_module, "get_database", database)
    assert await service.requeue_due_automatic_retries(now=now) == []
    assert task.status == TaskStatus.RETRYING
    assert task.metadata_["automatic_retry"]["status"] == "scheduled"
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_pause_preserves_completion_continuation_for_resume(service, monkeypatch):
    from src.domain import TaskType
    parent = SimpleNamespace(task_type=TaskType.EXPORT_DATA, status=TaskStatus.COMPLETED,
        input_data={"data_only": True, "data_line": "situation", "continuation_dirty_ids": [42]},
        metadata_={})
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: parent)))
    @asynccontextmanager
    async def database():
        yield db
    monkeypatch.setattr(release_module, "get_database", database)
    await service._continue_completed_release("parent")
    service.load_jobs.assert_not_awaited()
    assert parent.metadata_ == {}
    assert parent.input_data["continuation_dirty_ids"] == [42]
