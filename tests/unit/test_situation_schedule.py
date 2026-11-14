"""Situation samples follow the half-hour surveillance cadence and stay staggered."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.config import SituationSettings
from src.domain.task import TaskPriority, TaskStatus, TaskType
from src.services.global_sources.catalogue import SOURCE_REGISTRY
from src.services.situation_schedule_service import (
    assign_source_phases,
    next_phase,
    sample_job_id,
    select_due_samples,
    situation_schedule_service,
    source_upstream,
)


def _minute(value: datetime) -> int:
    current = value.astimezone(timezone.utc)
    elapsed = int((current - current.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() // 60)
    return elapsed % 30


def test_situation_sample_interval_matches_surveillance() -> None:
    settings = SituationSettings()
    assert settings.interval_minutes == 30
    assert settings.sample_pool_size == 2


def test_source_phases_keep_one_upstream_off_the_same_minute() -> None:
    phases = assign_source_phases(list(SOURCE_REGISTRY), interval=30)
    assert set(phases) == set(SOURCE_REGISTRY)
    assert len(set(phases.values())) == len(phases)
    by_upstream: dict[str, list[int]] = {}
    for source_id, phase in phases.items():
        by_upstream.setdefault(source_upstream(source_id), []).append(phase)
    for slots in by_upstream.values():
        assert len(slots) == len(set(slots))
    ecdc = by_upstream["ecdc.europa.eu"]
    assert min((ecdc[0] - ecdc[1]) % 30, (ecdc[1] - ecdc[0]) % 30) >= 10


def test_next_phase_is_strictly_later_on_the_same_offset() -> None:
    now = datetime(2026, 11, 14, 17, 5, 30, tzinfo=timezone.utc)
    scheduled = next_phase(now, 7, interval=30)
    assert scheduled == datetime(2026, 11, 14, 17, 7, tzinfo=timezone.utc)
    assert _minute(scheduled) == 7
    again = next_phase(scheduled, 7, interval=30)
    assert again == datetime(2026, 11, 14, 17, 37, tzinfo=timezone.utc)


def test_sample_pool_keeps_a_shared_upstream_waiting() -> None:
    due = [("who_don", 1), ("who_wer", 3), ("africa_cdc", 4), ("ecdc_events", 8)]
    chosen = select_due_samples(due, [], pool_size=2)
    assert chosen == ["who_don", "africa_cdc"]
    waiting = select_due_samples(due, [["who_don"]], pool_size=2)
    assert waiting == ["africa_cdc"]
    assert select_due_samples(due, [[]], pool_size=2) == []


async def test_manual_sample_is_one_normal_priority_task(monkeypatch) -> None:
    created: dict = {}

    async def create_task(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(task_uuid="sample-1")

    async def save(*_args, **_kwargs):
        return None

    async def idle():
        return []

    monkeypatch.setattr(situation_schedule_service, "_active_tasks", idle)
    monkeypatch.setattr(
        "src.services.situation_schedule_service.task_manager.create_task",
        create_task,
    )
    monkeypatch.setattr(
        "src.services.situation_schedule_service.schedule_state_repository.save",
        save,
    )
    result = await situation_schedule_service.trigger_job(sample_job_id("ecdc_events"), manual=True)
    assert result == {"job_id": "situation-sample-ecdc_events", "status": "queued", "task_uuid": "sample-1"}
    assert created["task_type"] == TaskType.REFRESH_SITUATION_SOURCES
    assert created["priority"] == TaskPriority.NORMAL
    assert created["status"] == TaskStatus.QUEUED
    payload = created["input_data"]
    assert payload["sources"] == ["ecdc_events"]
    assert payload["skip_ai"] is True
    assert payload["rebuild"] is False
    assert payload["fetch"] is True
    assert payload["situation_role"] == "sample"


async def test_reading_task_does_not_collect_feeds(monkeypatch) -> None:
    created: dict = {}

    async def create_task(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(task_uuid="read-1")

    async def save(*_args, **_kwargs):
        return None

    async def idle():
        return []

    monkeypatch.setattr(situation_schedule_service, "_active_tasks", idle)
    monkeypatch.setattr(
        "src.services.situation_schedule_service.task_manager.create_task",
        create_task,
    )
    monkeypatch.setattr(
        "src.services.situation_schedule_service.schedule_state_repository.save",
        save,
    )
    result = await situation_schedule_service.trigger_job("situation-read", manual=False)
    assert result["status"] == "queued"
    payload = created["input_data"]
    assert payload["fetch"] is False
    assert payload["skip_ai"] is False
    assert payload["rebuild"] is True
    assert payload["situation_role"] == "read"
    assert payload["scheduled_trigger"] is True
