"""Daily/weekly feeds dispatch independently of annual notification tables."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import canonicalize_task_source, scope_from_data_source
from src.ontology import load_disease_ontology
from src.services.crawl_service import CrawlService


FEEDS = [
    ("MY", "my_idengue_daily", "SRC_MY_IDENGUE_DAILY", "MalaysiaIDengueDailyUpdater", "my_dengue"),
    ("MY", "who_flunet_weekly", "SRC_MY_WHO_FLUNET", "MYWHOFluNetUpdater", "my_who_flunet"),
    ("MY", "who_gho_annual", "SRC_MY_WHO_GHO", "MYWHOAnnualUpdater", "my_who_annual"),
    ("NL", "rivm_virological_weekly", "SRC_NL_RIVM_VIROLOGICAL", "NLVirologicalWeeklyUpdater", "nl_weekly"),
    ("NL", "rivm_respiratory_weekly", "SRC_NL_RIVM_RESPIRATORY", "NLRespiratoryWeeklyUpdater", "nl_weekly"),
]


@pytest.mark.parametrize("country,scope,source_id,updater_name,module", FEEDS)
async def test_current_source_dispatch_preserves_connector_and_identity(
    monkeypatch, country, scope, source_id, updater_name, module,
):
    from src.services.ingestion import runner

    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    result = await CrawlService().execute(
        task=SimpleNamespace(input_data={"start_year": None}),
        country_code=country, source=scope, force=False,
        process=True, save_raw=True, fill_missing=False,
    )
    assert result is run.return_value
    args = run.call_args.kwargs
    assert args["connector"].__name__ == f"src.services.ingestion.countries.{module}"
    assert args["source"] == scope
    assert args["updater"].ontology_source_id == source_id
    assert args["updater"].mapping_notifications_enabled is False
    assert args["updater"].public_release_enabled is False
    assert args["connector_options"] == {}
    assert canonicalize_task_source(scope, country_code=country) == scope
    assert scope_from_data_source(source_id) == scope
    config = get_country_bootstrap_config(country)
    assert scope in config["crawler_config"]["sources"]
    assert config["crawler_config"]["source_policies"][scope]["public_release_enabled"] is False
    assert any(row["id"] == source_id for row in load_disease_ontology().to_dict()["sources"])


@pytest.mark.parametrize("country,scope,source_id,updater_name,module", FEEDS)
async def test_current_sources_reject_synthetic_gap_filling(
    monkeypatch, country, scope, source_id, updater_name, module,
):
    from src.services.ingestion import runner

    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    with pytest.raises(ValueError, match="fill_missing"):
        await CrawlService().execute(
            task=SimpleNamespace(input_data={}), country_code=country,
            source=scope, force=False, process=True,
            save_raw=True, fill_missing=True,
        )
    run.assert_not_awaited()


def test_idengue_source_flow_preserves_regional_geographies():
    from dashboard.api.routers.sources import _OBSERVATION_GEOGRAPHY_SERIES_SOURCES

    updater = CrawlService._make_updater("MalaysiaIDengueDailyUpdater", "MY")
    assert updater.series_geography_from_rows is True
    assert updater.series_geography_key is None
    assert "SRC_MY_IDENGUE_DAILY" in _OBSERVATION_GEOGRAPHY_SERIES_SOURCES


def test_who_source_policies_preserve_history_before_std_start_year():
    policies = get_country_bootstrap_config("MY")["crawler_config"]["source_policies"]
    assert policies["who_flunet_weekly"]["full_history_start_year"] == 1996
    assert policies["who_gho_annual"]["full_history_start_year"] == 1954
