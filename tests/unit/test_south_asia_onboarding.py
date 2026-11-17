"""Country dispatch, source identity and disabled publication/scheduling gates."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from importlib import import_module

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.source_channels import source_channel
from src.core.source_scopes import canonical_data_source_label, canonicalize_task_source, scope_from_data_source, source_options_for_country
from src.core.south_asia_weekly_sources import SOUTH_ASIA_WEEKLY_SOURCES as SOUTH_ASIA_SOURCES
from src.services.crawl_service import CrawlService


@pytest.mark.parametrize("code,contract", SOUTH_ASIA_SOURCES.items())
async def test_source_and_task_dispatch_round_trip(monkeypatch, code, contract):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    scope = contract["scope"]
    assert canonicalize_task_source("all", country_code=code) == scope
    for label in (scope, contract["label_en"], contract["source_id"]):
        assert scope_from_data_source(label) == scope
        assert canonical_data_source_label(label, country_code=code) == contract["label_en"]
    crawler_module = import_module(f"src.data.crawlers.{contract['module']}")
    assert scope_from_data_source(crawler_module.DEFAULT_SOURCE_NAME) == scope
    assert [o["value"] for o in source_options_for_country(code)] == [scope]
    cfg = get_country_bootstrap_config(code)
    assert cfg["public_release_enabled"] is False
    assert cfg["crawler_config"]["public_release_enabled"] is False
    assert source_channel(code, scope)["enabled_by_default"] is False
    updater = CrawlService._make_updater(contract["updater"], code)
    assert updater.public_release_enabled is False
    assert updater.ontology_source_id == contract["source_id"]
    value = await CrawlService().execute(task=SimpleNamespace(input_data={"max_reports": 3}), country_code=code,
        source=scope, force=False, process=True, save_raw=True, fill_missing=False)
    assert value is run.return_value
    options = run.call_args.kwargs
    assert options["connector"].__name__ == f"src.services.ingestion.countries.{contract['module']}"
    assert options["connector_options"]["start_year"] == updater.full_history_start_year
    assert options["connector_options"]["max_reports"] == 3
    assert options["source"] == scope
