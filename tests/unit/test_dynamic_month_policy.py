from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from dashboard.api.routers.sources import _country_source_config
from dashboard.api.schemas.sources import AutomationJobCreate
from src.domain.automation_job import AutomationJob
from src.services.automation_service import AutomationJobConfig
from src.services.crawl_service import CrawlService
from src.services.crawl_task_service import CrawlTaskService


def _country(code: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        code=code,
        name=code,
        name_en=code,
        name_local=code,
        language="en",
        timezone="UTC",
    )


def test_europe_source_policy_is_exposed_to_the_control_plane() -> None:
    fi = _country_source_config(_country("FI"), lang="en")
    se = _country_source_config(_country("SE"), lang="en")

    assert fi.source_policy.supports_current_month is True
    assert fi.source_policy.default_include_current_month is True
    assert fi.source_policy.dynamic_revision_enabled is True
    assert fi.source_policy.default_revision_window_months == 3
    assert fi.source_policy.current_month_status == "provisional"
    assert fi.source_policy.public_release_enabled is True

    assert se.source_policy.current_month_status == (
        "provisional_when_source_evidence_exists"
    )
    assert se.source_policy.public_release_enabled is True
    assert se.source_policy.public_release_editable is False


def test_at_de_revision_windows_use_source_period_units() -> None:
    at = _country_source_config(_country("AT"), lang="en")
    de = _country_source_config(_country("DE"), lang="en")

    assert at.supports_crawl is True
    assert at.source_policy.temporal_granularity == "monthly"
    assert at.source_policy.revision_window_unit == "months"
    assert at.source_policy.default_revision_window_months == 3

    assert de.supports_crawl is True
    assert de.source_policy.temporal_granularity == "weekly"
    assert de.source_policy.revision_window_unit == "weeks"
    assert de.source_policy.default_revision_window_months == 12


def test_ie_control_plane_exposes_current_and_annual_source_policies() -> None:
    ie = _country_source_config(_country("IE"), lang="en")
    options = {option.value: option for option in ie.source_options}

    assert list(options) == ["hpsc_ndh", "hpsc_weekly_archive", "hpsc_annual"]
    assert options["hpsc_ndh"].default_start_year == 2021
    assert options["hpsc_ndh"].source_policy.revision_window_unit == "weeks"
    assert options["hpsc_ndh"].source_policy.default_revision_window == 12
    assert options["hpsc_annual"].source_kind == "history"
    assert options["hpsc_annual"].default_start_year == 2004
    assert options["hpsc_annual"].history_end_year == 2020
    assert options["hpsc_annual"].source_policy.temporal_granularity == "annual"
    assert options["hpsc_annual"].source_policy.dynamic_revision_enabled is False
    assert options["hpsc_annual"].source_policy.public_release_enabled is False
    assert options["hpsc_weekly_archive"].source_kind == "history"
    assert options["hpsc_weekly_archive"].default_start_year == 2015
    assert options["hpsc_weekly_archive"].history_end_year == 2021
    assert options["hpsc_weekly_archive"].source_policy.temporal_granularity == "weekly"
    assert options["hpsc_weekly_archive"].source_policy.public_release_enabled is False


def test_automation_contract_persists_dynamic_month_policy() -> None:
    payload = AutomationJobCreate(
        job_id="fi-dynamic",
        name="FI Dynamic",
        country_code="FI",
        interval_minutes=1440,
        include_current_month=True,
        revision_window_months=6,
    )
    config = AutomationJobConfig.from_dict(
        payload.model_dump(),
        default_tz="UTC",
        default_retry_threshold=3,
    )

    assert config.include_current_month is True
    assert config.revision_window_months == 6
    assert "include_current_month" in AutomationJob.__table__.columns
    assert "revision_window_months" in AutomationJob.__table__.columns


def test_crawl_service_applies_control_plane_policy_to_updater() -> None:
    updater = SimpleNamespace(
        include_current_month=False,
        refresh_recent_months=3,
    )

    CrawlService._configure_monthly_runtime_policy(
        updater,
        country_code="FI",
        include_current_month=True,
        revision_window_months=8,
    )

    assert updater.include_current_month is True
    assert updater.refresh_recent_months == 8


def test_crawl_service_applies_weekly_revision_policy() -> None:
    updater = SimpleNamespace(refresh_recent_weeks=12)

    CrawlService._configure_monthly_runtime_policy(
        updater,
        country_code="DE",
        include_current_month=False,
        revision_window_months=26,
    )

    assert updater.refresh_recent_weeks == 26


@pytest.mark.asyncio
async def test_crawl_task_snapshot_contains_resolved_dynamic_policy(monkeypatch) -> None:
    service = CrawlTaskService()
    country = _country("FI")
    captured: dict = {}

    class Result:
        def scalar_one_or_none(self):
            return None

    class Database:
        async def execute(self, _query):
            return Result()

    @asynccontextmanager
    async def database():
        yield Database()

    async def resolve_country(**_kwargs):
        return country

    async def create_task(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(task_uuid="task-1")

    async def update_status(_task_uuid, _status):
        return SimpleNamespace(task_uuid="task-1")

    monkeypatch.setattr(
        "src.services.crawl_task_service.get_database", database
    )
    monkeypatch.setattr(service, "_resolve_country", resolve_country)
    monkeypatch.setattr(
        "src.services.crawl_task_service.task_manager.create_task", create_task
    )
    monkeypatch.setattr(
        "src.services.crawl_task_service.task_manager.update_task_status",
        update_status,
    )

    await service.enqueue_crawl_task(
        country_code="FI",
        source="thl_ttr",
        include_current_month=True,
        revision_window_months=5,
    )

    assert captured["input_data"]["include_current_month"] is True
    assert captured["input_data"]["revision_window_months"] == 5
