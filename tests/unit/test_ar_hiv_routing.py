"""Keep AR annual HIV capture separate from BEN cumulative archives."""

from types import SimpleNamespace

import pytest

from src.services.crawl_service import CrawlService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_handler", "expected_updater", "expected_scope"),
    [
        ("all", "hiv", "ArgentinaHIVAnnualUpdater", "ar_moh_hiv_annual"),
        ("ar_moh_hiv_annual", "hiv", "ArgentinaHIVAnnualUpdater", "ar_moh_hiv_annual"),
        ("SRC_AR_MOH_HIV_ANNUAL", "hiv", "ArgentinaHIVAnnualUpdater", "ar_moh_hiv_annual"),
        ("ar_ben_weekly", "ben", "ArgentinaBENUpdater", "ar_ben_weekly"),
        ("SRC_AR_BEN", "ben", "ArgentinaBENUpdater", "ar_ben_weekly"),
    ],
)
async def test_default_and_explicit_ar_sources_choose_independent_pipelines(
    monkeypatch, source, expected_handler, expected_updater, expected_scope,
):
    service = CrawlService()
    selected = []

    def updater(name, country):
        selected.append((name, country))
        return SimpleNamespace()

    async def hiv(**kwargs):
        return ("hiv", kwargs["source"])

    async def ben(**kwargs):
        return ("ben", kwargs["source"])

    monkeypatch.setattr(service, "_make_updater", updater)
    monkeypatch.setattr(service, "_configure_monthly_runtime_policy", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_execute_ar_hiv_annual", hiv)
    monkeypatch.setattr(service, "_execute_ar_surveillance", ben)
    result = await service.execute(
        task=SimpleNamespace(input_data={}), country_code="AR", source=source,
        force=False, process=True, save_raw=True, fill_missing=False,
    )
    assert result == (expected_handler, expected_scope)
    assert selected == [(expected_updater, "AR")]


@pytest.mark.asyncio
async def test_routine_hiv_check_replays_published_history_instead_of_empty_recent_window(monkeypatch):
    from src.services.ingestion import runner
    from src.data.processors.ar_hiv import ArgentinaHIVAnnualUpdater

    observed = {}

    async def run(service, **kwargs):
        observed.update(kwargs)
        return "collected"

    monkeypatch.setattr(runner, "run_surveillance", run)
    service = CrawlService()
    result = await service._execute_ar_hiv_annual(
        task=SimpleNamespace(input_data={"scheduled_trigger": True, "revision_window_months": 3}),
        updater=ArgentinaHIVAnnualUpdater(), source="ar_moh_hiv_annual",
        force=False, process=True, save_raw=True, fill_missing=False,
    )
    assert result == "collected"
    assert observed["connector"].__name__.endswith(".ar_hiv")
    assert observed["connector_options"]["start_year"] == 2014


def test_archive_replay_has_separate_ar_hiv_contract():
    from scripts.imports.import_surveillance_archive import COUNTRIES, SOURCE_OVERRIDES

    assert COUNTRIES["AR"] == ("ArgentinaBENUpdater", "ar_ben_weekly")
    assert SOURCE_OVERRIDES[("AR", "ar_moh_hiv_annual")] == ("ArgentinaHIVAnnualUpdater", "ar_hiv")


def test_saved_hiv_job_describes_complete_history_replay():
    from scripts.configure_priority_country_updates import desired_jobs

    job = desired_jobs()["ar-ar-moh-hiv-annual-daily"]
    assert job["source"] == "ar_moh_hiv_annual"
    assert "Full published history is reread for revisions." in job["notes"]
    assert "Older history uses a separate full replay" not in job["notes"]
