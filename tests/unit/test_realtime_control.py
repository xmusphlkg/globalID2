"""Realtime API reads and actions isolate channels and retain publication safety."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dashboard.api.schemas.realtime import RealtimeOverviewOut, RealtimeRunOut
from src.domain import Base, Country, Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty
from src.services.data_release import realtime_control as module
from src.services.data_release.channel_control import ChannelControlStore

NOW = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
SECRET = "postgresql://private-user:secret-password@private-host/private-db"


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'realtime.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync,
            tables=[Country.__table__, Task.__table__, PublishDirty.__table__]))
    monkeypatch.setattr(module, "get_country_bootstrap_config", lambda code: {"public_release_enabled": code != "IE"})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all([Country(code=code, name=code, name_en=code, is_active=active)
            for code, active in (("JP", False), ("JP-25", True), ("US", True), ("IE", True))])
        for index, (line, scope) in enumerate((("surveillance", "JP"), ("surveillance", "JP-25"),
                ("surveillance", "US"), ("surveillance", "IE"), ("situation", "*"), ("situation", "source:who_don"))):
            db.add(PublishDirty(line=line, scope_key=scope, source_task_uuid=f"source-{index}",
                marked_at=NOW - timedelta(seconds=600 - index),
                change_set={"version": 1, "precise": True, "disease_ids": ["D038"], "changes": []} if scope == "JP-25" else None))
        db.add_all([
            Task(task_uuid="surv-old", task_type=TaskType.EXPORT_DATA, task_name="Old JSON", status=TaskStatus.COMPLETED,
                progress=65,
                input_data={"data_only": True, "release_job_id": "site-release", "publish_dirty_ids": [1], "incremental_country_codes": ["JP-25"]},
                output_data={"release_id": "data-surv-old", "manifest_sha256": "a" * 64},
                created_at=NOW - timedelta(minutes=4), completed_at=NOW - timedelta(minutes=3)),
            Task(task_uuid="surv-failed", task_type=TaskType.EXPORT_DATA, task_name="Failed JSON", status=TaskStatus.FAILED,
                input_data={"data_only": True, "data_line": "surveillance", "release_job_id": "site-release"},
                metadata_={"automatic_retry": {"status": "terminal", "category": SECRET, "last_error": SECRET}},
                last_error=SECRET, created_at=NOW - timedelta(minutes=2), completed_at=NOW - timedelta(minutes=1)),
            Task(task_uuid="situation-retry", task_type=TaskType.EXPORT_DATA, task_name="Situation JSON", status=TaskStatus.RETRYING,
                input_data={"data_only": True, "data_line": "situation", "release_job_id": "site-release", "publish_dirty_ids": [5], "continuation_dirty_ids": [7, 8]},
                metadata_={"automatic_retry": {"status": "scheduled", "scheduled_attempts": 2, "max_attempts": 3,
                    "next_attempt_at": (NOW + timedelta(minutes=5)).isoformat(), "category": "transient_external", "last_error": SECRET},
                    "release_continuation": {"status": "queued", "task_uuid": "child-run"}}, created_at=NOW),
            Task(task_uuid="static-newest", task_type=TaskType.EXPORT_DATA, task_name="Static", status=TaskStatus.COMPLETED,
                input_data={"content_only": True}, output_data={"release_id": "static-secret"},
                created_at=NOW + timedelta(minutes=1), completed_at=NOW + timedelta(minutes=1)),
        ])
        await db.commit()
    async with maker() as db:
        yield db
    await engine.dispose()


def service(tmp_path, *, enabled=True, paused=False, release=None):
    if release is not None and not hasattr(release, "load_jobs"):
        release.load_jobs = AsyncMock(return_value=[SimpleNamespace(job_id="site-release", enabled=True)])
    return module.RealtimeControlService(monitor_path=tmp_path / "monitor.json",
        settings_reader=lambda: SimpleNamespace(surveillance_json_enabled=enabled, situation_json_enabled=enabled),
        controls_reader=lambda: {channel: {"paused": paused, "updated_at": NOW.isoformat(), "error_code": None} for channel in module.CHANNELS},
        release_service=release)


async def test_pending_filters_before_pagination_and_matches_strict_planner(database, tmp_path):
    control = service(tmp_path)
    result = await control.pending(database, "surveillance", page=1, page_size=1, eligibility="blocked", now=NOW)
    assert result["total"] == 2 and result["total_pages"] == 2
    assert result["items"][0]["scope_key"] == "JP" and result["items"][0]["blocked_reason"] == "inactive_country"
    # An inactive coarse parent cannot become publishable merely because a
    # descendant is active: use the actual queue planner's strict contract.
    second = await control.pending(database, "surveillance", page=2, page_size=1, eligibility="blocked", now=NOW)
    assert second["items"][0]["blocked_reason"] == "disabled_country"
    published = await control.pending(database, "surveillance", eligibility="publishable", now=NOW)
    assert published["total"] == 2
    assert {item["scope_key"] for item in published["items"]} == {"JP-25", "US"}
    assert published["items"][0]["precise"] and published["items"][0]["disease_ids"] == ["D038"]


async def test_situation_eligibility_filters_global_and_preserves_invalid_markers(database, tmp_path):
    control = service(tmp_path)
    accepted = await control.pending(database, "situation", eligibility="publishable", now=NOW)
    blocked = await control.pending(database, "situation", eligibility="blocked", now=NOW)
    assert accepted["total"] == blocked["total"] == 1
    assert accepted["items"][0]["scope_key"] == "*"
    assert blocked["items"][0]["blocked_reason"] == "invalid_scope"
    assert blocked["items"][0]["scope_key"] == "<invalid-scope>"
    assert all(row is None for row in (await database.execute(select(PublishDirty.published_at))).scalars())


async def test_same_scope_coarse_marker_expands_every_member_without_loading_entire_queue(database, tmp_path):
    parent = (await database.execute(select(Country).where(Country.code == "JP"))).scalar_one()
    parent.is_active = True
    database.add(PublishDirty(line="surveillance", scope_key="JP", source_task_uuid="precise-parent",
        marked_at=NOW, change_set={"version": 1, "precise": True}))
    await database.commit()
    result = await service(tmp_path).pending(database, "surveillance", eligibility="publishable", page_size=100, now=NOW)
    parent_items = [item for item in result["items"] if item["scope_key"] == "JP"]
    assert len(parent_items) == 2
    assert {item["affected_country_count"] for item in parent_items} == {2}


async def test_run_pages_isolate_legacy_json_and_never_expose_errors_or_static_tasks(database, tmp_path):
    control = service(tmp_path)
    runs = await control.runs(database, "surveillance", page_size=1)
    assert runs["total"] == 2 and runs["items"][0]["task_uuid"] == "surv-failed"
    assert runs["items"][0]["retry"]["category"] == "unknown"
    assert SECRET not in json.dumps(runs)
    assert "static-secret" not in json.dumps(runs)
    older = await control.runs(database, "surveillance", page=2, page_size=1)
    assert older["items"][0]["task_uuid"] == "surv-old"
    assert older["items"][0]["progress"] == 100
    situation = await control.runs(database, "situation", statuses=[TaskStatus.RETRYING])
    run = RealtimeRunOut.model_validate(situation["items"][0])
    assert run.retry.scheduled_attempts == 2 and run.continuation.remaining_count == 2
    assert run.continuation.task_uuid == "child-run"
    assert run.href == "/publish/tasks?detail=situation-retry"


def snapshot():
    summary = {"count": 0, "scope_count": 0, "oldest_at": None, "oldest_age_seconds": None}
    return {channel: {"pending": summary, "publishable_pending": summary, "blocked_pending": summary,
        "queue": {"pending": 0, "queued": 0, "running": 0, "retrying": 1 if channel == "situation" else 0},
        "failed_last_24h": 0} for channel in module.CHANNELS}


async def test_overview_reads_local_monitor_and_db_only_with_gate_pause_and_staleness(database, tmp_path, monkeypatch):
    control = service(tmp_path, paused=True)
    payload = {"schema_version": 1, "checked_at": (NOW - timedelta(seconds=181)).isoformat(),
        "channels": {channel: {"status": "warning", "issues": [{"level": "warning", "code": SECRET, "path": SECRET}],
            "public": {"available": True, "release_id": "data-known", "manifest_sha256": "b" * 64,
                "generated_at": "2020-01-01T00:00:00Z", "http_status": 200, "sample_verified": True,
                "private": SECRET}} for channel in module.CHANNELS}}
    control.monitor_path.write_text(json.dumps(payload))
    collect = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(module, "collect_database_snapshot", collect)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("Overview must not contact Cloudflare"))
    result = RealtimeOverviewOut.model_validate(await control.overview(database, now=NOW))
    collect.assert_awaited_once_with(database, now=NOW)
    channel = result.channels["surveillance"]
    assert channel.enabled and channel.paused and not channel.effective_enabled and channel.manual_enabled
    assert channel.latest_success.task_uuid == "surv-old"
    assert channel.latest_success.progress == 100
    assert channel.latest_failure.task_uuid == "surv-failed"
    assert channel.monitor.stale and channel.monitor.http_status == 200 and channel.monitor.sample_verified
    assert channel.manifest.stale and channel.manifest.sha256 == "b" * 64
    assert {item.code for item in channel.monitor.issues} == {"monitor_unknown_issue", "monitor_stale"}
    assert SECRET not in result.model_dump_json()
    assert result.channels["situation"].current_run.task_uuid == "situation-retry"


async def test_cancelled_task_remains_in_history_without_being_called_a_failure(database, tmp_path, monkeypatch):
    database.add(Task(task_uuid="surv-cancelled", task_type=TaskType.EXPORT_DATA,
        task_name="Cancelled JSON", status=TaskStatus.CANCELLED, progress=40,
        input_data={"data_only": True, "data_line": "surveillance"},
        created_at=NOW + timedelta(minutes=3), completed_at=NOW + timedelta(minutes=3)))
    await database.commit()
    control = service(tmp_path)
    monkeypatch.setattr(module, "collect_database_snapshot", AsyncMock(return_value=snapshot()))
    overview = await control.overview(database, now=NOW + timedelta(minutes=5))
    assert overview["channels"]["surveillance"]["latest_failure"]["task_uuid"] == "surv-failed"
    history = await control.runs(database, "surveillance")
    cancelled = next(item for item in history["items"] if item["task_uuid"] == "surv-cancelled")
    assert cancelled["status"] == "cancelled" and cancelled["progress"] == 40


@pytest.mark.parametrize("state,expected", [(None, "monitor_missing"), ("broken", "monitor_unreadable"), ("[]", "monitor_invalid")])
def test_missing_or_corrupt_monitor_does_not_look_like_a_verified_live_pointer(tmp_path, state, expected):
    path = tmp_path / "monitor.json"
    if state is not None: path.write_text(state)
    payload, error = module._monitor_receipt(path)
    monitor, manifest = module.project_monitor(payload, error, "surveillance", now=NOW)
    assert monitor["stale"] and monitor["status"] == "unknown"
    assert monitor["issues"][0]["code"] == expected
    assert manifest["available"] is None and manifest["release_id"] is None


def test_partial_monitor_file_does_not_claim_the_other_channel_was_checked():
    monitor, manifest = module.project_monitor({"checked_at": NOW.isoformat(), "channels": {"situation": {}}},
        None, "surveillance", now=NOW)
    assert monitor["stale"] and monitor["status"] == "unknown"
    assert monitor["issues"] == [{"level": "warning", "code": "monitor_channel_missing"}]
    assert manifest["available"] is None


@pytest.mark.parametrize("retry", [False, True])
async def test_manual_action_while_paused_only_queues_own_json_channel(tmp_path, retry):
    release = SimpleNamespace(trigger_job=AsyncMock(return_value={"status": "queued", "task_uuid": "new-json"}))
    result = await service(tmp_path, paused=True, release=release).enqueue("situation", retry=retry)
    assert result["status"] == "queued" and result["href"] == "/publish/tasks?detail=new-json"
    release.trigger_job.assert_awaited_once_with("site-release", manual=True,
        trigger="dashboard-realtime-retry" if retry else "dashboard-realtime", data_only=True, data_line="situation")


async def test_missing_job_id_uses_first_enabled_configured_job_and_explicit_id_is_preserved(tmp_path):
    release = SimpleNamespace(trigger_job=AsyncMock(return_value={"status": "queued", "task_uuid": "new-json"}),
        load_jobs=AsyncMock(return_value=[SimpleNamespace(job_id="old-disabled", enabled=False), SimpleNamespace(job_id="actual-release", enabled=True)]))
    control = service(tmp_path, release=release)
    await control.enqueue("surveillance")
    assert release.trigger_job.call_args.args == ("actual-release",)
    release.load_jobs.reset_mock()
    await control.enqueue("surveillance", job_id="explicit-release")
    assert release.trigger_job.call_args.args == ("explicit-release",)
    release.load_jobs.assert_not_awaited()


async def test_no_enabled_job_is_a_clear_conflict_not_a_static_fallback(tmp_path):
    release = SimpleNamespace(trigger_job=AsyncMock(), load_jobs=AsyncMock(return_value=[]))
    with pytest.raises(module.RealtimeControlError) as error:
        await service(tmp_path, release=release).enqueue("situation")
    assert error.value.code == "no_enabled_release_job"
    release.trigger_job.assert_not_awaited()


@pytest.mark.parametrize("error,code,status", [(ValueError("Data release job not found: private"), "release_job_not_found", 404),
    (ValueError(SECRET), "realtime_selection_invalid", 422), (OSError(SECRET), "realtime_queue_unavailable", 503)])
async def test_action_failures_only_return_finite_codes(tmp_path, error, code, status):
    release = SimpleNamespace(trigger_job=AsyncMock(side_effect=error))
    with pytest.raises(module.RealtimeControlError) as caught:
        await service(tmp_path, release=release).enqueue("surveillance", job_id="explicit")
    assert caught.value.code == code and caught.value.status_code == status
    assert SECRET not in str(caught.value)


async def test_disabled_gate_cannot_be_overridden_by_manual_action(tmp_path):
    release = SimpleNamespace(trigger_job=AsyncMock())
    with pytest.raises(module.RealtimeControlError) as error:
        await service(tmp_path, enabled=False, release=release).enqueue("surveillance")
    assert error.value.code == "realtime_channel_disabled" and error.value.status_code == 409
    release.trigger_job.assert_not_awaited()


async def test_retry_reports_existing_reservation_and_never_requeues_an_old_task(tmp_path):
    release = SimpleNamespace(trigger_job=AsyncMock(return_value={"status": "skipped", "reason": "already_running", "task_uuid": "reserved"}))
    with pytest.raises(module.RealtimeControlError) as error:
        await service(tmp_path, release=release).enqueue("surveillance", retry=True)
    assert error.value.status_code == 409 and error.value.code == "realtime_task_reserved"
    assert error.value.href == "/publish/tasks?detail=reserved"
    assert release.trigger_job.await_count == 1


async def test_control_is_durable_without_modifying_environment_or_deployment_gate(tmp_path):
    store = ChannelControlStore(tmp_path / "control.json")
    async def write(channel, paused): return store.set_paused(channel, paused)
    control = module.RealtimeControlService(settings_reader=lambda: SimpleNamespace(surveillance_json_enabled=True),
        controls_reader=store.snapshot, control_writer=write)
    result = await control.control("surveillance", paused=True)
    assert result["enabled"] and result["paused"] and not result["effective_enabled"]
    assert not ChannelControlStore(store.path).snapshot()["situation"]["paused"]
    result = await control.control("surveillance", paused=False)
    assert result["effective_enabled"]


@pytest.mark.parametrize("code", ["control_state_invalid", "control_state_unavailable"])
def test_control_errors_fail_closed_and_keep_their_distinct_machine_code(tmp_path, code):
    control = service(tmp_path)
    control.controls_reader = lambda: {channel: {"paused": True, "error_code": code} for channel in module.CHANNELS}
    assert control._controls()["situation"]["error_code"] == code
