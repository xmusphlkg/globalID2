"""BQ is a private geography of NL collection, with no independent crawl."""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config, get_country_profile
from src.data.crawlers.nl_notifiable import DEFAULT_SCOPE, ONTOLOGY_SOURCE_ID
from src.data.processors.nl_notifiable import NLNotifiableAnnualUpdater
from src.services.crawl_service import CrawlService
from src.services.data_release.planning import plan_changes


def test_bq_registration_keeps_parent_collection_and_source_identity():
    config = get_country_bootstrap_config("BQ")
    crawler = config["crawler_config"]
    assert config["parent_country_code"] == "NL"
    assert crawler["collection_country_code"] == "NL"
    assert crawler["collection_source"] == DEFAULT_SCOPE
    assert crawler["source_system"] == ONTOLOGY_SOURCE_ID
    assert crawler["sources"] == []
    assert "BQ" not in CrawlService.supported_country_codes()
    parent = NLNotifiableAnnualUpdater()
    assert parent.series_country_code == "NL" and parent.series_geography_from_rows
    profile = get_country_profile("BQ")
    assert profile.name_en == "Caribbean Netherlands"
    assert profile.timezone == "America/Kralendijk"


def test_bq_pending_changes_remain_private_and_parent_refresh_stays_scheduled():
    from scripts.configure_national_notifications import desired_jobs

    config = get_country_bootstrap_config("BQ")
    assert config["public_release_enabled"] is False
    assert config["public_legacy_enabled"] is False
    assert config["crawler_config"]["public_release_enabled"] is False
    available = {code for code in ("NL", "BQ")
                 if get_country_bootstrap_config(code).get("public_release_enabled", True)}
    pending = SimpleNamespace(id=1, line="surveillance", scope_key="BQ", change_set=None)
    plan = plan_changes([pending], available)
    assert plan["country_codes"] == [] and plan["dirty_ids"] == []
    assert plan["deferred_change_count"] == 1
    jobs = desired_jobs()
    parent = jobs["nl-rivm-notifiable-daily"]
    assert parent["country_code"] == "NL" and parent["source"] == DEFAULT_SCOPE
    assert parent["enabled"] and parent["process"]
    assert not any(job["country_code"] == "BQ" for job in jobs.values())


@pytest.mark.parametrize("apply", [False, True])
async def test_targeted_nl_rephase_changes_only_schedule_times(monkeypatch, apply):
    from scripts import rephase_nl_refreshes as module

    old = datetime(2026, 11, 16, tzinfo=timezone.utc)
    jobs = [SimpleNamespace(job_id=job, source=scope, country_code="NL", enabled=True,
                            interval_minutes=30, daily_time=None, retry_threshold=3)
            for job, scope in module.JOBS]
    states = [SimpleNamespace(job_id=job, next_run_at=old, last_task_uuid="existing-task",
                              last_status="queued") for job, _ in module.JOBS]
    before_jobs = [vars(job).copy() for job in jobs]
    before_states = [vars(state).copy() for state in states]
    results = [SimpleNamespace(scalar_one=lambda: 1)]
    for values in ([], jobs, states):
        results.append(SimpleNamespace(scalars=lambda values=values: SimpleNamespace(all=lambda: values)))
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(module, "get_db", database)
    result = await module.rephase(apply=apply)
    assert result["status"] == ("armed" if apply else "preview")
    assert [vars(job) for job in jobs] == before_jobs
    for index, state in enumerate(states):
        assert state.last_task_uuid == before_states[index]["last_task_uuid"]
        assert state.last_status == before_states[index]["last_status"]
        assert state.next_run_at == (result["jobs"][index]["next_run_at"] if apply else old)
    dates = [row["next_run_at"] for row in result["jobs"]]
    assert all((dates[index + 1] - dates[index]) == timedelta(minutes=6) for index in range(3))
    assert dates[-1] - dates[0] < timedelta(minutes=30)


@pytest.mark.parametrize("changed_field,value", [
    ("source", "ecdc_atlas_annual"), ("automation_job_id", "nl-ecdc-daily"),
    ("country_code", "BQ"), ("scheduled_trigger", False),
])
async def test_bq_refresh_rejects_deduplicated_task_from_another_job(monkeypatch, changed_field, value):
    from scripts import rephase_nl_refreshes as module
    from src.domain.task import TaskStatus

    now = datetime.now(timezone.utc)
    inputs = {"country_code": "NL", "source": "rivm_notifiable_annual",
              "automation_job_id": "nl-rivm-notifiable-daily", "scheduled_trigger": True}
    inputs[changed_field] = value
    task = SimpleNamespace(input_data=inputs, created_at=now, status=TaskStatus.COMPLETED)
    state = SimpleNamespace(last_task_uuid="another-source-task")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one=lambda: state), SimpleNamespace(scalar_one_or_none=lambda: task)]))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(module, "get_db", database)
    result = await module.verify({"armed_at": (now - timedelta(minutes=1)).isoformat()})
    assert result["status"] == "pending" and not result["task_matches_job"]
    assert db.execute.await_count == 2
