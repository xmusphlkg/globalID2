from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.report_schedule_service import (
    ReportScheduleService,
    ReportWindow,
    complete_reporting_period,
    frequency_is_fresh,
    infer_frequency_from_dates,
    normalize_frequency,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,country,process,expected", [
    (True, "JP", True, True), (False, "JP", True, False),
    (True, "US", True, False), (True, "JP", False, False),
])
async def test_completed_crawl_checks_report_windows_immediately(monkeypatch, enabled, country, process, expected):
    service = ReportScheduleService()
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(schedule_enabled=enabled))
    trigger = AsyncMock()
    monkeypatch.setattr(service, "trigger_job", trigger)
    await service.maybe_trigger_after_crawl(SimpleNamespace(input_data={"country_code": country, "process": process}))
    if expected:
        trigger.assert_awaited_once_with(service.JOB_ID, manual=False)
    else:
        trigger.assert_not_awaited()


def test_normalizes_declared_period_names():
    assert normalize_frequency("yearly") == "ANNUAL"
    assert normalize_frequency("Quarterly") == "QUARTERLY"
    assert normalize_frequency("unknown") is None


def test_maps_latest_week_to_a_complete_week():
    assert complete_reporting_period("WEEKLY", date(2026, 10, 17), date(2026, 11, 2)) == (
        date(2026, 10, 12),
        date(2026, 10, 18),
    )


def test_does_not_publish_open_daily_or_weekly_periods():
    today = date(2026, 11, 2)
    assert complete_reporting_period("DAILY", today, today) == (
        date(2026, 11, 1),
        date(2026, 11, 1),
    )
    assert complete_reporting_period("WEEKLY", today, today) == (
        date(2026, 10, 26),
        date(2026, 11, 1),
    )


def test_monthly_quarterly_and_annual_windows_are_calendar_periods():
    today = date(2026, 11, 2)
    assert complete_reporting_period("MONTHLY", date(2026, 11, 1), today) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert complete_reporting_period("QUARTERLY", date(2026, 10, 1), today) == (
        date(2026, 7, 1),
        date(2026, 9, 30),
    )
    assert complete_reporting_period("ANNUAL", date(2026, 1, 1), today) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
    )


def test_freshness_limits_and_legacy_interval_inference():
    today = date(2026, 11, 2)
    assert frequency_is_fresh("WEEKLY", date(2026, 10, 17), today)
    assert not frequency_is_fresh("WEEKLY", date(2026, 10, 1), today)
    assert infer_frequency_from_dates([date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15)]) == "WEEKLY"


@pytest.mark.asyncio
async def test_enqueue_limits_to_japan_and_deduplicates_existing_periods(monkeypatch):
    service = ReportScheduleService()
    cfg = SimpleNamespace(schedule_max_active_tasks=2, schedule_max_tasks_per_tick=2)
    monkeypatch.setattr(service, "_config", lambda: cfg)
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 11, 3, tzinfo=timezone.utc))
    windows = [ReportWindow(index, code, code, "WEEKLY", date(2026, 10, 12), date(2026, 10, 12), date(2026, 10, 18), 80) for index, code in enumerate(("JP", "US"), 1)]
    discover = AsyncMock(return_value=windows)
    monkeypatch.setattr(service, "_country_report_windows", discover)
    monkeypatch.setattr(service, "_active_scheduled_tasks", AsyncMock(return_value=[]))
    existing = AsyncMock(return_value=set())
    monkeypatch.setattr(service, "_existing_report_windows", existing)
    create = AsyncMock(return_value=SimpleNamespace(task_uuid="jp-task"))
    monkeypatch.setattr("src.services.report_schedule_service.task_manager.create_task", create)
    result = await service._enqueue_due_reports(manual=True)
    discover.assert_awaited_once_with(allow_stale=True)
    assert len(result["queued"]) == 1
    assert create.await_args.kwargs["input_data"]["country"] == "JP"
    assert create.await_args.kwargs["input_data"]["enable_review"] is True
    existing.return_value = {(1, "weekly", date(2026, 10, 12))}
    create.reset_mock()
    result = await service._enqueue_due_reports(manual=False)
    assert result["queued"] == []
    create.assert_not_awaited()
    discover.assert_awaited_with(allow_stale=False)


@pytest.mark.asyncio
async def test_active_period_is_not_enqueued_twice(monkeypatch):
    service = ReportScheduleService()
    window = ReportWindow(1, "JP", "Japan", "WEEKLY", date(2026, 10, 12), date(2026, 10, 12), date(2026, 10, 18), 80)
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(schedule_max_active_tasks=3, schedule_max_tasks_per_tick=2))
    monkeypatch.setattr(service, "_country_report_windows", AsyncMock(return_value=[window]))
    monkeypatch.setattr(service, "_existing_report_windows", AsyncMock(return_value=set()))
    monkeypatch.setattr(service, "_active_scheduled_tasks", AsyncMock(return_value=[SimpleNamespace(input_data={"report_schedule_key": window.schedule_key})]))
    create = AsyncMock()
    monkeypatch.setattr("src.services.report_schedule_service.task_manager.create_task", create)
    assert (await service._enqueue_due_reports(manual=True))["queued"] == []
    create.assert_not_awaited()


def test_manual_runs_also_block_duplicate_scheduled_generation():
    keys = ReportScheduleService._active_window_keys([
        SimpleNamespace(input_data={"report_type": "weekly", "period_start": "2026-10-12T00:00:00Z"}),
        SimpleNamespace(input_data={"days": 30}),
    ])
    assert keys == {"JP:WEEKLY:2026-10-12", "JP:*"}
