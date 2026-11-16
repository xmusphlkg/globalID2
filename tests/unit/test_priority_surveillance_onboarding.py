"""Task dispatch and archive integrity for the new national surveillance sources."""
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import canonical_data_source_label, canonicalize_task_source, scope_from_data_source, source_options_for_country
from src.services.crawl_service import CrawlService

SOURCES = [
    ('CL', 'cl_eno_historical', 'ChileENOHistoricalUpdater', 'Chile MINSAL ENO Historical Notifications'),
    ('CO', 'co_ins_sivigila_historical', 'ColombiaINSSivigilaUpdater', 'Colombia INS SIVIGILA Historical Public Health Surveillance'),
    ('MW', 'mw_phim_idsr_weekly', 'MalawiPHIMIDSRUpdater', 'Malawi PHIM IDSR Weekly Bulletin'),
    ('ZA', 'za_nicd_nmc', 'SouthAfricaNICDNMCUpdater', 'South Africa NICD NMC'),
    ('ID', 'id_kemkes_profile', 'IndonesiaKemkesProfileUpdater', 'Indonesia Ministry of Health Annual Health Profile'),
    ('MX', 'mx_dge_annual', 'MexicoDGEAnnualUpdater', 'Mexico DGE Annual Morbidity'),
    ('NG', 'ncdc_surveillance', 'NigeriaNCDCUpdater', 'Nigeria NCDC Surveillance'),
    ('KH', 'kh_camewarn_weekly', 'CambodiaCamEWARNWeeklyUpdater', 'Cambodia CDC CamEWARN Weekly Surveillance'),
    ('AR', 'ar_ben_weekly', 'ArgentinaBENUpdater', 'Argentina National Epidemiological Bulletin (BEN)'),
    ('PE', 'pe_cdc_weekly', 'PeruCDCWeeklyUpdater', 'Peru CDC Epidemiological Bulletin'),
]


@pytest.mark.parametrize('country,scope,updater_name,label', SOURCES)
async def test_country_task_roundtrip_uses_canonical_archive_connector(monkeypatch, country, scope, updater_name, label):
    from src.services.ingestion import runner
    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, 'run_surveillance', run)
    assert canonicalize_task_source('all', country_code=country) == scope
    assert scope_from_data_source(label) == scope
    assert scope_from_data_source(scope) == scope
    assert canonical_data_source_label(scope, country_code=country) == label
    assert {option['value'] for option in source_options_for_country(country)} == {scope}
    config = get_country_bootstrap_config(country)
    assert config['public_release_enabled'] is False
    updater = CrawlService._make_updater(updater_name, country)
    value = await CrawlService().execute(
        task=SimpleNamespace(input_data={'start_year': max(2020, updater.full_history_start_year)}), country_code=country,
        source=scope, force=True, process=True, save_raw=True, fill_missing=False,
    )
    assert value is run.return_value
    args = run.call_args.kwargs
    assert args['source'] == scope
    if country == 'CO':
        assert args['connector'].__name__ == 'src.services.ingestion.co_update_gate'
        assert args['connector_options'] == {'force': True}
        assert args['process'] is False
    else:
        assert args['connector'].__name__ == 'src.services.ingestion.countries.' + country.lower()
        assert args['connector_options']['start_year'] == max(2020, updater.full_history_start_year)
        assert args['process'] is True
    assert args['updater'].ontology_source_id == updater.ontology_source_id
    # These integrations intentionally delegate writes to the shared native
    # store. Their legacy hook must still return a valid result to that runner.
    legacy_result = await updater.import_rows(None, [], source_latest_date=None)
    assert legacy_result.inserted_or_updated == 0


async def test_archive_refresh_rejects_unsupported_gap_fill_before_network(monkeypatch):
    from src.services.ingestion import runner
    run = AsyncMock()
    monkeypatch.setattr(runner, 'run_surveillance', run)
    with pytest.raises(ValueError, match='fill_missing'):
        await CrawlService()._execute_priority_surveillance(
            'ng', task=SimpleNamespace(input_data={}), updater=SimpleNamespace(full_history_start_year=2016),
            source='ncdc_surveillance', force=False, process=True, save_raw=True, fill_missing=True,
        )
    run.assert_not_called()


def archive_script():
    path = Path(__file__).resolve().parents[2] / 'scripts/imports/import_surveillance_archive.py'
    spec = spec_from_file_location('archive_import_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_manifest_checks_every_original_before_mutation(tmp_path, monkeypatch):
    module = archive_script()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    directory = tmp_path / 'data/raw/NG'.lower()
    directory.mkdir(parents=True)
    payload = b'%PDF original source bytes'
    raw = directory / 'report.pdf'
    raw.write_bytes(payload)
    record = {'relative_path': str(raw.relative_to(tmp_path)), 'sha256': hashlib.sha256(payload).hexdigest(),
        'byte_size': len(payload), 'source_url': 'https://ncdc.gov.ng/report.pdf', 'kind': 'ng_ncdc_pdf',
        'metadata_': {'source_year': 2020}}
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps({'artifacts': [record]}))
    artifacts = module.load_archive_manifest(manifest, 'NG')
    assert artifacts[0].metadata_ == record['metadata_']
    raw.write_bytes(b'%PDF corrupted source bytes')
    with pytest.raises(ValueError, match='integrity'):
        module.load_archive_manifest(manifest, 'NG')


def test_archive_manifest_rejects_cross_country_paths(tmp_path, monkeypatch):
    module = archive_script()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    directory = tmp_path / 'data/raw/ng'
    directory.mkdir(parents=True)
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps([{'relative_path': 'data/raw/ar/report.pdf'}]))
    with pytest.raises(ValueError, match='escapes'):
        module.load_archive_manifest(manifest, 'NG')
