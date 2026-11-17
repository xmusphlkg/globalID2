"""Dispatch every independently attributed source for BD/BT/MV/AF."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.source_channels import source_channel
from src.core.source_scopes import canonical_data_source_label, canonicalize_task_source, scope_from_data_source, source_options_for_country
from src.core.south_asia_sources import SOUTH_ASIA_ALL_SOURCES, SOUTH_ASIA_FOUR_COUNTRIES, south_asia_source
from src.services.crawl_service import CrawlService

SOURCES = {key: spec for key, spec in SOUTH_ASIA_ALL_SOURCES.items() if key[0] in SOUTH_ASIA_FOUR_COUNTRIES}


@pytest.mark.parametrize("key,spec", SOURCES.items())
async def test_source_identity_and_explicit_task_dispatch(monkeypatch, key, spec):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    code, scope = key
    assert canonicalize_task_source("all", country_code=code) == south_asia_source(code)["scope"]
    crawler = import_module(f"src.data.crawlers.{spec['module']}")
    source_name = getattr(crawler, "DEFAULT_SOURCE_NAME", None) or crawler.SOURCE_NAME
    for value in (scope, spec["label_en"], spec["source_id"], source_name):
        assert scope_from_data_source(value) == scope
        assert canonical_data_source_label(value, country_code=code) == spec["label_en"]
    assert {o["value"] for o in source_options_for_country(code)} - {"all"} == {s for c, s in SOURCES if c == code}
    assert get_country_bootstrap_config(code)["public_release_enabled"] is False
    assert source_channel(code, scope)["source_id"] == spec["source_id"]
    updater = CrawlService._make_updater(spec["updater"], code)
    assert updater.ontology_source_id == spec["source_id"] and updater.public_release_enabled is False
    result = await CrawlService().execute(task=SimpleNamespace(input_data={"max_reports": 2}), country_code=code,
        source=scope, force=False, process=True, save_raw=True, fill_missing=False)
    assert result is run.return_value
    options = run.call_args.kwargs
    assert options["source"] == scope
    assert options["connector"].__name__ == f"src.services.ingestion.countries.{spec['module']}"
    assert options["connector_options"]["max_reports"] == 2
    assert options["connector_options"]["start_year"] == updater.full_history_start_year


@pytest.mark.parametrize("code", sorted(SOUTH_ASIA_FOUR_COUNTRIES))
async def test_default_task_uses_its_preferred_source(monkeypatch, code):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    await CrawlService().execute(task=SimpleNamespace(input_data={}), country_code=code,
        source="all", force=False, process=False, save_raw=True, fill_missing=False)
    assert run.call_args.kwargs["source"] == south_asia_source(code)["scope"]


async def test_source_archives_never_synthesize_missing_periods(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    with pytest.raises(ValueError, match="fill_missing"):
        await CrawlService().execute(task=SimpleNamespace(input_data={}), country_code="BT",
            source="all", force=False, process=True, save_raw=True, fill_missing=True)
    run.assert_not_called()
