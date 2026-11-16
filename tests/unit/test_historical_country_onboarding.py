"""Historical national feeds retain their own execution and release scopes."""
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config, get_country_display_name
from src.core.source_scopes import (
    canonicalize_task_source, get_expected_scopes_for_country,
    scope_from_data_source, source_options_for_country,
)
from src.ontology import load_disease_ontology
from src.services.crawl_service import CrawlService


@pytest.mark.parametrize("code,scope,source_id,updater_name,label", [
    ("TH", "th_doe506_monthly", "SRC_TH_DOE506", "ThailandDOE506MonthlyUpdater", "Thailand DOE Report 506 Monthly History"),
    ("MY", "moh_std_annual", "SRC_MY_MOH_STD", "MYAnnualUpdater", "Malaysia MOH Annual STD Notifications"),
    ("NL", "rivm_notifiable_annual", "SRC_NL_RIVM_NOTIFIABLE", "NLNotifiableAnnualUpdater", "Netherlands RIVM Annual Notifiable Disease Notifications"),
])
def test_historical_source_contracts(code, scope, source_id, updater_name, label):
    assert canonicalize_task_source("all", country_code=code) == ("th_dds_weekly" if code == "TH" else scope)
    assert scope_from_data_source(label) == scope
    assert scope in get_expected_scopes_for_country(code)
    assert scope in {option["value"] for option in source_options_for_country(code)}
    assert get_country_display_name(code, "zh") in {"泰国", "马来西亚", "荷兰"}
    updater = CrawlService._make_updater(updater_name, code)
    assert updater.ontology_source_id == source_id
    assert getattr(updater, "series_geography_from_rows", False) or updater.series_geography_key == f"country:{code}:national"
    sources = load_disease_ontology().to_dict()["sources"]
    assert any(source["id"] == source_id and source["country_code"] == code for source in sources)
    module = {"TH": "th", "MY": "my_notifiable", "NL": "nl_notifiable"}[code]
    from importlib import import_module
    assert scope_from_data_source(import_module(f"src.data.crawlers.{module}").DEFAULT_SOURCE_NAME) == scope


def test_netherlands_ecdc_release_remains_independent():
    config = get_country_bootstrap_config("NL")
    assert set(config["crawler_config"]["sources"]) == {"rivm_notifiable_annual", "ecdc_atlas_annual", "rivm_virological_weekly", "rivm_respiratory_weekly"}
    # The merger keeps the verified ECDC feed public; the unverified native
    # feed must still be excluded by the explicit source-system allowlist.
    assert config["public_source_systems"] == ["SRC_NL_ECDC_ATLAS"]
    assert config["public_legacy_enabled"] is False
    policies = config["crawler_config"]["source_policies"]
    assert policies["rivm_notifiable_annual"]["public_release_enabled"] is False
    assert policies["ecdc_atlas_annual"]["public_release_enabled"] is True
    assert get_country_bootstrap_config("MY")["public_release_enabled"] is False


@pytest.mark.parametrize("scope,source_id,updater_name,module", [
    ("th_dds_weekly", "SRC_TH_DDS_WEEKLY", "ThailandDDSWeeklyUpdater", "th_dds"),
    ("th_dds_tableau", "SRC_TH_DDS_TABLEAU", "ThailandDDSTableauUpdater", "th_tableau"),
    ("th_id_th_history", "SRC_TH_ID_TH_ARCHIVE", "ThailandIDTHHistoryUpdater", "th_id_th"),
])
def test_thailand_independent_source_registrations(scope, source_id, updater_name, module):
    from importlib import import_module

    updater = CrawlService._make_updater(updater_name, "TH")
    assert updater.ontology_source_id == source_id
    assert updater.mapping_notifications_enabled is False
    assert updater.public_release_enabled is False
    label = import_module(f"src.data.crawlers.{module}").DEFAULT_SOURCE_NAME
    assert scope_from_data_source(label) == scope
    assert scope in get_expected_scopes_for_country("TH")
    assert any(source["id"] == source_id for source in load_disease_ontology().to_dict()["sources"])


@pytest.mark.parametrize("code,module,updater_name,scope", [
    ("MY", "my_notifiable", "MYAnnualUpdater", "moh_std_annual"),
    ("NL", "nl_notifiable", "NLNotifiableAnnualUpdater", "rivm_notifiable_annual"),
])
async def test_historical_task_dispatch_preserves_source_and_start_year(monkeypatch, code, module, updater_name, scope):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    updater = CrawlService._make_updater(updater_name, code)
    result = await CrawlService()._execute_notifiable_annual(
        module, task=SimpleNamespace(input_data={"start_year": 2021}),
        source=scope, force=False, process=False, save_raw=True,
        fill_missing=False, updater=updater,
    )
    assert result is run.return_value
    options = run.call_args.kwargs
    assert options["connector"].__name__.endswith("." + module)
    assert options["source"] == scope
    assert options["connector_options"]["start_year"] == 2021


async def test_historical_snapshot_rejects_gap_fill_before_collection(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    with pytest.raises(ValueError, match="fill_missing"):
        await CrawlService()._execute_notifiable_annual(
            "my", task=SimpleNamespace(input_data={}), source="moh_std_annual",
            force=False, process=True, save_raw=True, fill_missing=True,
            updater=SimpleNamespace(country_code="MY"),
        )
    run.assert_not_called()


async def test_thailand_defaults_do_not_turn_null_task_options_into_invalid_years(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    await CrawlService()._execute_th_monthly(
        task=SimpleNamespace(input_data={"start_year": None, "end_year": None}),
        source="th_doe506_monthly", force=False, process=False,
        save_raw=True, fill_missing=False, updater=object(),
    )
    assert run.call_args.kwargs["connector_options"]["start_year"] is None
    assert run.call_args.kwargs["connector_options"]["end_year"] is None
    assert run.call_args.kwargs["connector_options"]["disease_codes"] is None


@pytest.mark.parametrize("source", ["th_doe506_monthly", "doe506", "th_506"])
async def test_thailand_history_dispatch_forwards_resume_cursor(monkeypatch, source):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    inputs = {"backfill_history": True, "report_offset": 216,
              "max_reports": 144, "resume_plan_hash": "captured-plan"}
    await CrawlService().execute(
        task=SimpleNamespace(input_data=inputs), country_code="TH",
        source=source, force=False, process=True,
        save_raw=True, fill_missing=False,
    )
    options = run.call_args.kwargs
    assert options["source"] == "th_doe506_monthly"
    assert options["updater"].ontology_source_id == "SRC_TH_DOE506"
    for key, value in inputs.items():
        assert options["connector_options"][key] == value


async def test_thailand_default_dispatches_current_dds_and_preserves_basis(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, "run_surveillance", run)
    await CrawlService().execute(
        task=SimpleNamespace(input_data={"basis": "gen", "weeks": [46]}),
        country_code="TH", source="all", force=False, process=True,
        save_raw=True, fill_missing=False,
    )
    options = run.call_args.kwargs
    assert options["updater"].ontology_source_id == "SRC_TH_DDS_WEEKLY"
    assert options["connector_options"] == {"weeks": [46], "basis": "gen"}
    assert set(get_expected_scopes_for_country("TH")) == {
        "th_dds_weekly", "th_dds_tableau", "th_doe506_monthly", "th_id_th_history",
    }


async def test_source_flow_counts_caribbean_observations_outside_netherlands():
    from dashboard.api.routers.sources import _OBSERVATION_GEOGRAPHY_SERIES_SOURCES
    from src.control_plane.source_flow_queries import load_series_flow_facts
    countries = [SimpleNamespace(id=1, code="NL", name_en="Netherlands"),
                 SimpleNamespace(id=2, code="BQ", name_en="Caribbean Netherlands")]
    source_id = "SRC_NL_RIVM_NOTIFIABLE"
    series = [SimpleNamespace(series_code=code, source_system=source_id,
              country_code="NL", availability_status="active",
              metric_type="case_notifications", mapping_relation="unmapped",
              comparability="not_comparable") for code in ["nl_case", "bq_case"]]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: countries), SimpleNamespace(all=lambda: series),
        SimpleNamespace(all=lambda: []), SimpleNamespace(all=lambda: []),
    ]))
    when = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rollup = AsyncMock(return_value=[("nl_case", "NL", "accepted", 10, when, when),
                                     ("bq_case", "BQ", "accepted", 20, when, when)])
    rows, *_ = await load_series_flow_facts(
        db, None, observation_rollup=rollup,
        geography_sources=_OBSERVATION_GEOGRAPHY_SERIES_SOURCES,
    )
    assert {row.country_code: row.observation_count for row in rows} == {"NL": 10, "BQ": 20}


async def test_ecdc_dispatch_stays_available_after_adding_national_netherlands(monkeypatch):
    service = CrawlService()
    ecdc = AsyncMock(return_value=object())
    native = AsyncMock()
    monkeypatch.setattr(service, "_execute_ecdc_annual", ecdc)
    monkeypatch.setattr(service, "_execute_nl_annual", native)
    result = await service.execute(
        task=SimpleNamespace(input_data={}), country_code="NL",
        source="ecdc_atlas_annual", force=False, process=False,
        save_raw=True, fill_missing=False,
    )
    assert result is ecdc.return_value
    assert ecdc.call_args.kwargs["updater"].ontology_source_id == "SRC_NL_ECDC_ATLAS"
    native.assert_not_called()
