"""Routing, source precedence and publication boundaries of the new feeds."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.configure_high_frequency_sources import configure
from scripts.import_high_frequency_sources import row_identity, source_tasks_conflict
from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES, make_updater
from src.core.source_scopes import canonicalize_task_source, default_source_for_country
from src.services.crawl_service import CrawlService


def test_registered_updaters_keep_country_source_and_measure_identities():
    for (country, scope), spec in HIGH_FREQUENCY_SOURCES.items():
        updater = make_updater(country, scope)
        assert updater.country_code == country
        assert updater.ontology_source_id == spec.source_id
        assert not updater.mapping_notifications_enabled
        assert updater.full_history_start_year == spec.start_year
    with pytest.raises(ValueError):
        make_updater("GB", "ecdc_respiratory_weekly")
    with pytest.raises(ValueError):
        make_updater("US", "mx_dge_weekly")


def test_config_does_not_restrict_existing_unlisted_public_sources():
    bootstrap = {"DE": {"crawler_config": {"sources": ["rki_survstat"]},
                         "public_source_scopes": ["rki_survstat"]},
                 "MX": {"crawler_config": {"sources": ["mx_dge_annual"]},
                         "public_source_systems": ["SRC_MX_DGE_ANNUAL"]}}
    channels = {"channels": []}
    configure(bootstrap, channels)
    assert "public_source_systems" not in bootstrap["DE"]
    assert bootstrap["DE"]["public_source_scopes"][0] == "rki_survstat"
    assert {"SRC_MX_DGE_ANNUAL", "SRC_MX_DGE_WEEKLY", "SRC_MX_DGE_MONTHLY"} <= set(bootstrap["MX"]["public_source_systems"])
    before = deepcopy((bootstrap, channels))
    configure(bootstrap, channels)
    assert (bootstrap, channels) == before


@pytest.mark.parametrize("country,source", [
    ("MX", "mx_dge_weekly"), ("CZ", "cz_szu_monthly"), ("EE", "ee_terviseamet_monthly"),
])
async def test_preferred_default_routes_to_durable_high_frequency_handler(monkeypatch, country, source):
    handler = AsyncMock(return_value="durable-result")
    monkeypatch.setattr(CrawlService, "_execute_high_frequency", handler)
    service = CrawlService()
    assert default_source_for_country(country) == source
    assert canonicalize_task_source("all", country_code=country) == source
    result = await service.execute(SimpleNamespace(input_data={}), country, "all", False, True, True, False)
    assert result == "durable-result"
    assert handler.call_args.kwargs["source"] == source
    assert handler.call_args.kwargs["updater"].country_code == country
    assert canonicalize_task_source("mx_dge_annual", country_code="MX") == "mx_dge_annual"


async def test_normal_refresh_is_bounded_and_explicit_replay_remains_available(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock(return_value="stored")
    monkeypatch.setattr(runner, "run_surveillance", run)
    service = CrawlService()
    updater = make_updater("EE", "ee_terviseamet_monthly")
    await service._execute_high_frequency(task=SimpleNamespace(input_data={}),
        source="ee_terviseamet_monthly", updater=updater, force=False,
        process=True, save_raw=True, fill_missing=False)
    assert run.call_args.kwargs["connector_options"]["start_year"] >= 2026
    with pytest.raises(ValueError, match="synthesize"):
        await service._execute_high_frequency(task=SimpleNamespace(input_data={}),
            source="ee_terviseamet_monthly", updater=updater, force=False,
            process=True, save_raw=True, fill_missing=True)


def test_acceptance_identity_preserves_overlapping_strata_and_task_reservations():
    row = {"Date": "2026-11-02", "SourceDiseaseCode": "INF_ALL", "Dimensions": '{"surveillance_origin":"SENTINEL"}'}
    other = {**row, "Dimensions": '{"surveillance_origin":"NONSENTINEL"}'}
    assert row_identity(row) != row_identity(other)
    assert source_tasks_conflict("all", "who_flunet_weekly")
    assert source_tasks_conflict("who_flunet_weekly", "who_flunet_weekly")
    assert not source_tasks_conflict("ecdc_atlas_annual", "ecdc_respiratory_weekly")
