"""Saved update channels, bounded scheduled revisions and persistent jobs."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from scripts import configure_priority_country_updates as setup
from src.core.source_channels import load_source_channels, source_channel
from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES
from src.core.source_scopes import canonicalize_task_source, scope_from_data_source
from src.services.crawl_service import CrawlService


BASE_CHANNEL_CONTRACTS = {
    'NG': ('ncdc_surveillance', 'SRC_NG_NCDC'),
    'KH': ('kh_camewarn_weekly', 'SRC_KH_CAMEWARN'),
    'AR': ('ar_ben_weekly', 'SRC_AR_BEN'),
    'PE': ('pe_cdc_weekly', 'SRC_PE_CDC'),
    'ZA': ('za_nicd_nmc', 'SRC_ZA_NICD_NMC'),
    'ID': ('id_kemkes_profile', 'SRC_ID_KEMKES_PROFILE'),
    'MX': ('mx_dge_annual', 'SRC_MX_DGE_ANNUAL'),
    'CL': ('cl_eno_historical', 'SRC_CL_ENO'),
    'CO': ('co_ins_sivigila_historical', 'SRC_CO_INS_SIVIGILA'),
    'MW': ('mw_phim_idsr_weekly', 'SRC_MW_PHIM_IDSR'),
}
ADDITIONAL_CHANNEL_CONTRACTS = {
    ('AR', 'ar_moh_hiv_annual'): 'SRC_AR_MOH_HIV_ANNUAL',
    ('CO', 'co_ins_sivigila_current'): 'SRC_CO_INS_SIVIGILA_CURRENT',
    ('CO', 'co_ins_bes_weekly'): 'SRC_CO_INS_BES',
    ('CO', 'co_ins_sivigila_microdatos'): 'SRC_CO_INS_SIVIGILA_MICRODATOS',
}
EXPECTED_CHANNEL_CONTRACTS = {
    **{(country, scope): source_id for country, (scope, source_id) in BASE_CHANNEL_CONTRACTS.items()},
    **ADDITIONAL_CHANNEL_CONTRACTS,
}
EXPECTED_PUBLIC_CHANNELS = {
    ('NG', 'ncdc_surveillance'),
    ('KH', 'kh_camewarn_weekly'),
    ('AR', 'ar_moh_hiv_annual'),
    ('PE', 'pe_cdc_weekly'),
    ('ZA', 'za_nicd_nmc'),
    ('ID', 'id_kemkes_profile'),
    ('MX', 'mx_dge_annual'),
    ('CL', 'cl_eno_historical'),
    ('CO', 'co_ins_sivigila_historical'),
    ('MW', 'mw_phim_idsr_weekly'),
}
EXPECTED_PUBLIC_CHANNELS.update(HIGH_FREQUENCY_SOURCES)


def test_official_channels_match_source_registration():
    channels = load_source_channels()
    base_channels = [c for c in channels if c['country_code'] in BASE_CHANNEL_CONTRACTS]
    actual = {(c['country_code'], c['scope']): c['source_id'] for c in base_channels}
    assert EXPECTED_CHANNEL_CONTRACTS.items() <= actual.items()
    for channel in channels:
        assert source_channel(channel['country_code'], channel['scope']) == channel
        assert scope_from_data_source(channel['source_id']) == channel['scope']
        assert channel['discovery_urls'] and all(url.startswith('https://') for url in channel['discovery_urls'])
        assert ZoneInfo(channel['timezone'])
    assert source_channel('US', 'nndss_api') is None
    channel = source_channel('PE', 'all')
    assert channel['discovery_method'] == 'POST' and channel['discovery_form']['bq_categoria'] == '1'
    channel['discovery_urls'].clear()
    assert source_channel('PE', 'all')['discovery_urls']  # callers cannot mutate the saved catalogue


@pytest.mark.parametrize('country', ['NG', 'KH', 'AR', 'PE', 'ZA', 'ID', 'MX', 'CL', 'CO', 'MW'])
@pytest.mark.parametrize('explicit_start,force,expected_full', [(None, False, False), (None, True, True), (2023, False, True)])
async def test_scheduled_window_and_explicit_full_replay(monkeypatch, country, explicit_start, force, expected_full):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, 'run_surveillance', run)
    channel = source_channel(country, 'all')
    updater = SimpleNamespace(country_code=country, full_history_start_year=1980)
    supplied = {'scheduled_trigger': True, 'revision_window_months': channel['refresh_years']}
    if explicit_start:
        supplied['start_year'] = explicit_start
    await CrawlService()._execute_priority_surveillance(country.lower(),
        task=SimpleNamespace(input_data=supplied), updater=updater, source=channel['scope'],
        force=force, process=True, save_raw=True, fill_missing=False)
    actual = run.call_args.kwargs['connector_options']['start_year']
    assert actual == (explicit_start or 1980) if expected_full else actual == datetime.now(timezone.utc).year - channel['refresh_years'] + 1


def database(monkeypatch, rows):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))), add=Mock(), commit=AsyncMock())
    @asynccontextmanager
    async def fake():
        yield db
    monkeypatch.setattr(setup, 'get_db', fake)
    monkeypatch.setattr(setup, 'automation_service', SimpleNamespace(reschedule_job=AsyncMock()))
    return db


def test_jobs_keep_native_cadence_and_raw_evidence():
    jobs = setup.desired_jobs()
    assert len(jobs) == len(load_source_channels())
    actual = {key: (job['country_code'], job['source']) for key, job in jobs.items()
        if job['country_code'] in BASE_CHANNEL_CONTRACTS}
    expected = {
            f"{code.lower()}-{scope.replace('_', '-')}-daily": (code, scope)
        for code, scope in EXPECTED_CHANNEL_CONTRACTS}
    assert expected.items() <= actual.items()
    historical_job = jobs['co-co-ins-sivigila-historical-daily']
    current_job = jobs['co-co-ins-sivigila-current-daily']
    bes_job = jobs['co-co-ins-bes-weekly-daily']
    assert historical_job['enabled']
    assert 'Daily metadata and year-count check' in historical_job['notes']
    assert 'requires_full_backfill' in historical_job['notes']
    assert 'Full published history is reread for revisions' in current_job['notes']
    assert current_job['revision_window_months'] == 4
    assert not bes_job['enabled']
    assert 'hourly at :45 America/Bogota' in bes_job['notes']
    assert '24-hour cache TTL' in bes_job['notes']
    assert 'legacy daily scheduler entry stays disabled' in bes_job['notes']
    assert source_channel('CO', 'co_ins_bes_weekly')['integration_status'] == 'automated_managed_runtime'
    assert source_channel('CO', 'co_ins_bes_weekly')['coverage_verified'] is False
    for job in jobs.values():
        assert canonicalize_task_source(job['source'], country_code=job['country_code']) == job['source']
        assert job['process'] and job['save_raw'] and not job['force'] and not job['fill_missing']
        if job['country_code'] == 'AR' and job['source'] == 'ar_moh_hiv_annual':
            assert job['revision_window_months'] == 24
        else:
            assert job['revision_window_months'] in {1, 2, 3, 4}
        assert job['daily_time'] and job['interval_minutes'] is None


def test_managed_colombia_bes_panel_shows_transport_status_without_credentials(monkeypatch):
    from dashboard.api.routers.sources import _source_channel_for_panel
    from dashboard.api.schemas.sources import SourceChannelOut
    from src.data.crawlers import co_ins_transport
    monkeypatch.setattr(co_ins_transport, 'ins_transport_readiness', lambda: {
        'configured': True, 'proxy_kind': 'http', 'dependency_ready': True,
    })
    channel = SourceChannelOut.model_validate(_source_channel_for_panel('CO', 'co_ins_bes_weekly'))
    payload = channel.model_dump()
    assert payload['integration_status'] == 'automated_managed_runtime'
    assert payload['coverage_verified'] is False
    assert payload['transport_readiness'] == {
        'configured': True, 'proxy_kind': 'http', 'dependency_ready': True,
    }
    assert 'proxy_url' not in str(payload) and 'credentials' not in str(payload)


async def test_preview_does_not_change_jobs(monkeypatch):
    db = database(monkeypatch, [])
    changes = await setup.configure()
    assert len(changes) == len(load_source_channels())
    assert {change.job_id for change in changes} == set(setup.desired_jobs())
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    setup.automation_service.reschedule_job.assert_not_awaited()


async def test_apply_preserves_pauses_and_is_idempotent(monkeypatch):
    jobs = setup.desired_jobs()
    rows = [SimpleNamespace(job_id=key, **{**value, 'enabled': False}) for key, value in jobs.items()]
    db = database(monkeypatch, rows)
    changes = await setup.configure(apply=True)
    assert all(x.action == 'unchanged' and not x.enabled for x in changes)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    setup.automation_service.reschedule_job.assert_not_awaited()
    await setup.configure(apply=True, enable=True)
    assert all(x.enabled for x in rows)
    assert setup.automation_service.reschedule_job.await_count == len(jobs)
    assert {call.args[0] for call in setup.automation_service.reschedule_job.await_args_list} == set(jobs)


async def test_targeted_bes_update_changes_only_its_paused_job(monkeypatch):
    jobs = setup.desired_jobs()
    rows = [SimpleNamespace(job_id=key, **value) for key, value in jobs.items()]
    bes = next(row for row in rows if row.source == 'co_ins_bes_weekly')
    bes.notes = 'Old transport and source contract status'
    before = {row.job_id: vars(row).copy() for row in rows if row is not bes}
    database(monkeypatch, rows)
    changes = await setup.configure(apply=True, countries=['CO'], sources=['co_ins_bes_weekly'])
    assert len(changes) == 1
    assert changes[0].job_id == bes.job_id and changes[0].action == 'update'
    assert not bes.enabled
    assert 'Automatic updates are active via globalid-co-bes-refresh.timer' in bes.notes
    assert {row.job_id: vars(row) for row in rows if row is not bes} == before
    setup.automation_service.reschedule_job.assert_awaited_once_with(bes.job_id)


@pytest.mark.parametrize('country', ['NG', 'KH', 'AR', 'PE', 'ZA', 'ID', 'MX', 'CL', 'CO', 'MW'])
def test_control_panel_has_saved_channel_and_annual_revision_units(country):
    from dashboard.api.routers.sources import _country_source_config
    from src.domain import Country
    country_row = Country(code=country, name=country, name_en=country, language='en', timezone='UTC')
    config = _country_source_config(country_row)
    channel = source_channel(country, 'all')
    assert config.supports_crawl
    option = next(x for x in config.source_options if x.value == channel['scope'])
    assert option.source_channel.official_url == channel['official_url']
    assert option.source_policy.revision_window_unit == 'years'
    assert option.source_policy.dynamic_revision_enabled
    assert not option.default_fill_missing
    assert option.source_policy.public_release_enabled is ((country, channel['scope']) in EXPECTED_PUBLIC_CHANNELS)
    if country == 'AR':
        ben = next(x for x in config.source_options if x.value == 'ar_ben_weekly')
        assert ben.source_channel.source_id == 'SRC_AR_BEN'
        assert ben.source_policy.public_release_enabled is False


async def test_large_stratified_archive_history_stays_under_database_bind_limit():
    from src.data.storage.series_observation_store import SeriesObservationStore, SeriesObservationQualityPolicy
    when = datetime(2025, 1, 1, tzinfo=timezone.utc)
    observations = [{'series_code': f'SER_MX_STRATUM_{i:05}', 'time': when} for i in range(33001)]
    sizes = []
    async def execute(statement):
        params = statement.compile().params
        codes = next(value for value in params.values() if isinstance(value, list))
        sizes.append(len(codes))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{'series_code': code, 'time': when} for code in codes]))
    rows = await SeriesObservationStore._load_recent_history(SimpleNamespace(execute=execute), observations, SeriesObservationQualityPolicy())
    assert len(rows) == len(observations)
    assert {r['series_code'] for r in rows} == {r['series_code'] for r in observations}
    assert max(sizes) < 32767 and len(sizes) > 1


async def test_nigeria_scheduled_collection_filters_old_report_routes_but_keeps_unknown_dates(monkeypatch):
    import json
    from src.services.ingestion.countries import ng
    seen = []
    weekly = 'https://ncdc.gov.ng/reports/weekly/2026/1/1'
    old = 'https://ncdc.gov.ng/reports/weekly/2020/1/1'
    category = 'https://ncdc.gov.ng/diseases/sitreps/?cat=99'
    references = [
        {'url': 'https://ncdc.gov.ng/old.pdf', 'kind': 'sitrep', 'title': 'Report 2020'},
        {'url': 'https://ncdc.gov.ng/current.pdf', 'kind': 'sitrep', 'title': 'Report 2026'},
        {'url': 'https://ncdc.gov.ng/unknown.pdf', 'kind': 'sitrep', 'title': 'Report without verified year'},
    ]
    class Crawler:
        session = SimpleNamespace(close=lambda: None)
        def get(self, url):
            seen.append(url)
            return SimpleNamespace(url=url, content=b'%PDF captured source' if url.endswith('.pdf') else b'official catalogue')
    class Writer:
        def __init__(self): self.artifacts = []
        async def write(self, payload, **metadata): self.artifacts.append((payload, metadata))
    monkeypatch.setattr(ng, 'NigeriaNCDCCrawler', Crawler)
    monkeypatch.setattr(ng, 'discover_weekly_pages', lambda payload: [old, weekly])
    monkeypatch.setattr(ng, 'discover_categories', lambda payload: [category])
    monkeypatch.setattr(ng, 'discover_reports', lambda payload, listing_url, kind: references if kind == 'sitrep' else [{'url': 'https://ncdc.gov.ng/weekly.pdf', 'year': 2026, 'kind': kind}])
    writer = Writer()
    await ng.collect(writer, start_year=2025)
    assert old not in seen and 'https://ncdc.gov.ng/old.pdf' not in seen
    assert weekly in seen and category in seen and 'https://ncdc.gov.ng/unknown.pdf' in seen
    plan = json.loads(next(p for p, m in writer.artifacts if m['kind'] == 'ng_ncdc_plan'))
    assert plan['requested_start_year'] == 2025
    assert len(plan['excluded_older_reports']) == 1
