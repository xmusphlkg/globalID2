"""Remaining sources round-trip country identity through tasks and refresh jobs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.remaining_country_sources import REMAINING_COUNTRIES, EXTRA_SOURCE_CONTRACTS, source_contract
from src.core.source_channels import load_source_channels
from src.core.source_scopes import canonical_data_source_label, canonicalize_task_source, scope_from_data_source, source_scope_label
from src.ontology import load_disease_ontology
from src.services.crawl_service import CrawlService


@pytest.mark.parametrize("code", REMAINING_COUNTRIES)
async def test_dispatch_preserves_country_source_and_full_history(monkeypatch, code):
    from src.services.ingestion import runner
    contract = source_contract(code)
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    result = await CrawlService().execute(
        task=SimpleNamespace(input_data={}), country_code=code, source=contract["scope"],
        force=False, process=True, save_raw=True, fill_missing=False,
        revision_window_months=1,
    )
    assert result is run.return_value
    args = run.call_args.kwargs
    assert args["source"] == contract["scope"]
    assert args["connector_options"] == {"country_code": code}
    assert args["updater"].country_code == code
    assert args["updater"].ontology_source_id == contract["source_id"]
    assert args["updater"].public_release_enabled is False
    assert args["updater"].mapping_notifications_enabled is False
    assert args["updater"].full_history_start_year == contract["start_year"]


@pytest.mark.parametrize("code", REMAINING_COUNTRIES)
def test_scope_labels_registry_and_refresh_contract(code):
    contract = source_contract(code)
    assert canonicalize_task_source(contract["scope"], country_code=code) == contract["scope"]
    assert scope_from_data_source(contract["source_id"]) == contract["scope"]
    assert scope_from_data_source(contract["label_en"]) == contract["scope"]
    assert source_scope_label(contract["scope"], country_code=code) == contract["label_en"]
    assert canonical_data_source_label(contract["source_id"], country_code=code) == contract["label_en"]
    source = next(row for row in load_disease_ontology().to_dict()["sources"] if row["id"] == contract["source_id"])
    assert source["country_code"] == code
    policy = get_country_bootstrap_config(code)["crawler_config"]["source_policies"][contract["scope"]]
    assert policy["refresh_strategy"] == "full_published_history_upsert"
    assert policy["public_release_enabled"] is True
    channel = next(row for row in load_source_channels() if row["country_code"] == code and row["scope"] == contract["scope"])
    assert channel["refresh_strategy"] == policy["refresh_strategy"]


async def test_explicit_manual_history_window_and_gap_rejection(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    options = dict(task=SimpleNamespace(input_data={"start_year": 1980}), country_code="KE",
                   source="who_gho_annual", force=False, process=True, save_raw=True)
    await CrawlService().execute(**options, fill_missing=False)
    assert run.call_args.kwargs["connector_options"] == {"country_code": "KE", "start_year": 1980}
    run.reset_mock()
    with pytest.raises(ValueError, match="fill_missing"):
        await CrawlService().execute(**options, fill_missing=True)
    run.assert_not_awaited()


@pytest.mark.parametrize("code,scope", EXTRA_SOURCE_CONTRACTS)
async def test_extra_sources_dispatch_separately_from_country_baseline(monkeypatch, code, scope):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    await CrawlService().execute(
        task=SimpleNamespace(input_data={"scheduled_trigger": True}),
        country_code=code, source=scope, force=False, process=True,
        save_raw=True, fill_missing=False, revision_window_months=2,
    )
    args = run.call_args.kwargs
    contract = source_contract(code, scope)
    assert args["source"] == scope
    assert args["updater"].country_code == code
    assert args["updater"].ontology_source_id == contract["source_id"]
    assert args["updater"].ontology_source_id != source_contract(code)["source_id"]
    assert args["updater"].full_history_start_year == contract["start_year"]
    assert args["connector_options"] == {"country_code": code, "force": False}
    assert args["updater"].public_release_enabled is False
    assert args["updater"].mapping_notifications_enabled is False


@pytest.mark.parametrize('code,scope,source_id', [
    ('CR', 'cr_moh_notifications', 'SRC_CR_MOH_NOTIFICATIONS'),
    ('LA', 'who_wpro_dengue', 'SRC_LA_WHO_WPRO_DENGUE'),
    ('VN', 'who_wpro_dengue', 'SRC_VN_WHO_WPRO_DENGUE'),
])
def test_three_reviewed_additions_have_exact_registry_scope_and_policy(code, scope, source_id):
    contract = source_contract(code, scope)
    assert contract['source_id'] == source_id
    assert canonicalize_task_source('who_gho_annual', country_code=code) == 'who_gho_annual'
    assert canonicalize_task_source(scope, country_code=code) == scope
    assert scope_from_data_source(source_id) == scope
    assert scope_from_data_source(contract['label_en']) == scope
    assert source_scope_label(scope, country_code=code) == contract['label_en']
    assert canonical_data_source_label(source_id, country_code=code) == contract['label_en']
    registered = next(row for row in load_disease_ontology().to_dict()['sources'] if row['id'] == source_id)
    assert registered['country_code'] == code
    channel = next(row for row in load_source_channels() if row['country_code'] == code and row['scope'] == scope)
    assert channel['source_id'] == source_id
    policy = get_country_bootstrap_config(code)['crawler_config']['source_policies'][scope]
    assert policy['public_release_enabled'] is False
    assert channel['refresh_strategy'] == policy['refresh_strategy']


def test_job_plan_describes_all_history_and_preserves_other_jobs():
    from scripts.configure_priority_country_updates import desired_jobs
    jobs = desired_jobs()
    for code in REMAINING_COUNTRIES:
        job = next(job for job in jobs.values() if job["country_code"] == code)
        assert job["process"] and job["save_raw"] and job["enabled"]
        assert not job["fill_missing"]
        assert "Full published history" in job["notes"]
        assert job["timezone"] == source_contract(code)["timezone"]


async def test_refresh_acceptance_preserves_old_history_but_rejects_changed_current_facts(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone
    from scripts import onboard_remaining_surveillance as onboarding

    def row(when, count):
        return {"Date": when, "SourceDiseaseCode": "dengue__weekly_cases",
                "GeographyKey": "country:LA:national", "Cases": str(count)}

    current, previous = row("2026-10-18", 232), row("2026-08-09", 100)
    stored = [(raw, float(raw["Cases"]), datetime.fromisoformat(raw["Date"]).replace(tzinfo=timezone.utc),
               raw["GeographyKey"]) for raw in [current, previous]]
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=lambda: stored)))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(onboarding, "get_db", database)
    monkeypatch.setattr(onboarding, "ROOT", tmp_path)
    (tmp_path / "data/current/la").mkdir(parents=True)
    updater = SimpleNamespace(ontology_source_id="SRC_LA_WHO_WPRO_DENGUE", source_scope="who_wpro_dengue")
    with pytest.raises(ValueError, match="database acceptance differs"):
        await onboarding.verify_rows("LA", updater, [current])
    accepted = await onboarding.verify_rows("LA", updater, [current], allow_retained_history=True)
    assert accepted["pass"] and accepted["retained_history_observations"] == 1
    with pytest.raises(ValueError, match="database acceptance differs"):
        await onboarding.verify_rows("LA", updater, [row("2026-10-18", 233)], allow_retained_history=True)
