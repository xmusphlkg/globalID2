"""Real WHO annual country contracts, full replay, and revision boundaries."""
import copy
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers import who_annual as source
from src.data.processors.who_annual import WHOAnnualUpdater
from src.services.ingestion.countries import who_annual as connector

FIXTURES = Path(__file__).parents[1] / 'fixtures/ingestion/WHO'


def metadata():
    return json.loads((FIXTURES / 'gho_metadata.json').read_text())['value']


def sample(code='KE'):
    return json.loads((FIXTURES / f'{code.lower()}_gho_sample.json').read_text())['value']


def parse(records=None, code='KE', **kwargs):
    records = sample(code) if records is None else records
    return source.parse_records([(r['IndicatorCode'], r, {}) for r in records], country_code=code,
                                metadata=metadata(), as_of=kwargs.pop('as_of', date(2026, 11, 16)), **kwargs)


def test_real_country_counts_retains_cases_deaths_zero_and_unknown():
    bykey = {(r['SourceDiseaseCode'], r['Year']): r for r in parse()}
    assert bykey['TB_c_newinc', '2025']['Cases'] == '88607'
    assert bykey['WHS3_62', '2025']['Cases'] == '751'
    assert bykey['CHOLERA_0000000001', '2016']['Cases'] == '5866'
    assert bykey['CHOLERA_0000000002', '2016']['Cases'] == '80'
    assert bykey['CHOLERA_0000000002', '2016']['Measure'] == 'death_notifications'
    assert bykey['WHS3_49', '2018']['Cases'] == '0'
    assert bykey['WHS3_41', '2025']['Cases'] == ''
    assert bykey['WHS3_41', '2025']['MissingValueStatus'] == 'source_missing'
    assert all(r['GeographyKey'] == 'country:KE:national' for r in bykey.values())
    et = {(r['SourceDiseaseCode'], r['Year']): r for r in parse(code='ET')}
    assert et['CHOLERA_0000000001', '2010']['Cases'] == '1682'  # numeric=null, legacy text count
    assert et['TB_c_newinc', '2025']['Cases'] == '150448'


@pytest.mark.parametrize('change', [
    {'SpatialDim':'ETH'}, {'SpatialDimType':'REGION'}, {'TimeDimType':'WEEK'},
    {'Dim1':'MLE', 'Dim1Type':'SEX'}, {'NumericValue':True}, {'NumericValue':-1},
    {'NumericValue':1.2}, {'Low':0}, {'High':10}, {'TimeDim':2026}, {'TimeDim':True},
])
def test_wrong_country_estimates_stratification_and_invalid_counts_fail(change):
    raw = copy.deepcopy(sample()[0]); raw.update(change)
    with pytest.raises(ValueError):
        parse([raw])


def test_country_and_indicator_allowlist_duplicate_and_closed_year():
    with pytest.raises(ValueError, match='Unreviewed'):
        source.data_url('WHS3_62', 'XX')
    with pytest.raises(ValueError, match='Unreviewed'):
        source.data_url('MODELLED_ESTIMATE', 'KE')
    raw = sample()[0]
    with pytest.raises(ValueError, match='Duplicate'):
        parse([raw, raw])
    latest = next(r for r in sample() if r['TimeDim'] == 2025)
    with pytest.raises(ValueError, match='closed calendar'):
        parse([latest], as_of=date(2025, 12, 31))
    assert parse([latest], as_of=date(2026, 1, 1))[0]['Year'] == '2025'


@pytest.mark.parametrize('numeric,text,status,value', [(None,'<5','source_suppressed','<5'),
    ('*',None,'source_suppressed','*'), (None,'no data','source_missing',''),
    (None,'1,234','reported','1234'), (None,'0','reported','0')])
def test_suppressed_and_missing_text_remain_explicit(numeric,text,status,value):
    raw = copy.deepcopy(sample()[0]); raw.update(NumericValue=numeric, Value=text)
    row = parse([raw])[0]
    assert (row['MissingValueStatus'],row['Cases']) == (status,value)


def artifact(tmp_path, payload, url, dataset):
    body = json.dumps({'value':payload}).encode()
    path = Path('data/raw/who-test') / f'{dataset}.json'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
    return SimpleNamespace(relative_path=path.as_posix(),sha256=hashlib.sha256(body).hexdigest(),
        byte_size=len(body),source_url=url,metadata_={'endpoint':'who_odata_page','dataset':dataset,
        'page_number':0,'requested_url':url,'retrieved_at':'2026-11-16T00:00:00Z'})


def captures(tmp_path, code='KE'):
    items = [artifact(tmp_path, metadata(), source.METADATA_URL, 'gho_indicators')]
    raw = sample(code)
    for indicator in source.INDICATORS:
        items.append(artifact(tmp_path,[r for r in raw if r['IndicatorCode']==indicator],
                              source.data_url(indicator,code),indicator))
    return items


def test_organizer_reads_all_captured_indicators_offline_and_retains_provenance(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('offline organizer used network'))
    items = captures(tmp_path)
    result = connector.organize(items, updater=WHOAnnualUpdater('KE'))
    assert len(result.rows) == len(sample())
    assert result.source_audit['queried_indicators'] == 18
    assert result.source_audit['source_indicators_with_records'] == 15
    assert 'WHS3_47' in result.source_audit['indicators_without_country_records']
    assert all(r['RawSHA256'] and r['SourceIndicatorMetadataSHA256'] and r['SourceRecordId'] for r in result.rows)
    with pytest.raises(ValueError,match='Missing captured'):
        connector.organize(items[:-1],updater=WHOAnnualUpdater('KE'))
    with pytest.raises(ValueError,match='disagrees'):
        connector.organize(items,updater=WHOAnnualUpdater('KE'),country_code='ET')


def test_source_year_is_pinned_to_captured_metadata(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    items = captures(tmp_path)
    items[0].metadata_['retrieved_at'] = '2025-12-31T23:59:59Z'
    with pytest.raises(ValueError,match='closed calendar'):
        connector.organize(items,updater=WHOAnnualUpdater('KE'))


def test_country_updaters_refresh_history_including_older_revisions_and_scope():
    rows = parse()
    updater = WHOAnnualUpdater('KE')
    result = updater.prepare_rows(rows,months=[(2026,11)])
    assert len(result.rows) == len(rows)
    assert updater.ontology_source_id == 'SRC_KE_WHO_GHO'
    assert updater.series_geography_key == 'country:KE:national'
    assert updater.public_release_enabled is updater.mapping_notifications_enabled is False
    with pytest.raises(ValueError):updater.normalize_source('ET')
    revised = copy.deepcopy(rows);revised[0]['Cases'] = '99'
    assert updater.prepare_rows(revised).rows[0]['Cases'] == '99'
