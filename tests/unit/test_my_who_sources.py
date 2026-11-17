"""WHO source grain, observed/missing counts, capture completeness and replay."""
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests

from src.data.crawlers import my_who_annual as annual, my_who_flunet as flu
from src.data.processors.my_who_annual import MYWHOAnnualUpdater
from src.data.processors.my_who_flunet import MYWHOFluNetUpdater
from src.services.ingestion.countries import my_who_common as common
from src.services.ingestion.countries import my_who_annual as annual_connector, my_who_flunet as flu_connector
from src.data.storage.series_observation_store import SeriesObservationStore

FIXTURES=Path(__file__).parents[1]/'fixtures/ingestion/MY'


def fixture(name):return json.loads((FIXTURES/name).read_bytes())['value']


def flu_parse(raw=None,**kwargs):
    return flu.parse_records([(r,{}) for r in (raw if raw is not None else fixture('who_flunet_sample.json'))],fields=flu.detection_fields(fixture('who_flunet_dictionary.json')),as_of=kwargs.pop('as_of',date(2026,11,16)),**kwargs)


def annual_parse(raw=None,**kwargs):
    source=raw if raw is not None else fixture('who_gho_sample.json')
    return annual.parse_records([(r['IndicatorCode'],r,{}) for r in source],metadata=fixture('who_gho_metadata.json'),as_of=kwargs.pop('as_of',date(2026,11,16)),**kwargs)


def test_real_flu_source_keeps_non_covid_pathogens_and_denominators():
    rows,audit=flu_parse()
    latest=[r for r in rows if r['Date']=='2026-10-19']
    bycode={r['SourceDiseaseCode']:r for r in latest}
    assert bycode['INF_ALL']['Cases']=='233' and bycode['INF_A']['Cases']=='182' and bycode['INF_B']['Cases']=='51'
    assert bycode['HUMAN_CORONA']['Cases']=='6' and 'not SARS-CoV-2' in bycode['HUMAN_CORONA']['RawDiseaseLabel']
    assert bycode['INF_ALL']['SourceInfluenzaSpecimensProcessed']==924
    assert bycode['RSV']['SourceRSVSpecimensProcessed']==5 and bycode['RSV']['Cases']=='0'
    assert all(r['Measure']=='laboratory_detections' and r['AggregationPolicy']=='non_additive' for r in rows)
    assert {r['SurveillanceOrigin'] for r in rows}=={'SENTINEL','NONSENTINEL','NOTDEFINED'}
    assert audit['missing_detection_cells']>0
    assert not any(r['SourceDiseaseCode']=='ILI_ACTIVITY' for r in rows)
    assert all(r['MissingValueStatus']=='source_missing' for r in rows if r['SourceDiseaseCode']=='ANOTSUBTYPABLE')


def test_flu_missing_is_explicit_zero_and_suppression_are_retained():
    raw=fixture('who_flunet_sample.json')[-1]
    raw.update({'INF_A':None,'INF_B':0,'INF_ALL':'<5'})
    rows,_=flu_parse([raw]);codes={r['SourceDiseaseCode']:r for r in rows}
    assert codes['INF_A']['Cases']=='' and codes['INF_A']['MissingValueStatus']=='source_missing'
    assert codes['INF_B']['Cases']=='0'
    assert codes['INF_ALL']['Cases']=='<5' and codes['INF_ALL']['MissingValueStatus']=='source_suppressed'


@pytest.mark.parametrize('change',[{'COUNTRY_CODE':'THA'},{'ISO_WEEK':2},{'ORIGIN_SOURCE':'NEW_UNKNOWN'},{'INF_ALL':-1},{'INF_ALL':2.1},{'INF_ALL':True}])
def test_flu_invalid_grain_and_values_fail_closed(change):
    raw=fixture('who_flunet_sample.json')[-1];raw.update(change)
    with pytest.raises(ValueError):flu_parse([raw])


def test_duplicate_flu_week_origin_and_changed_dictionary_fail():
    raw=fixture('who_flunet_sample.json')[-1]
    with pytest.raises(ValueError,match='Duplicate'):flu_parse([raw,raw])
    dictionary=fixture('who_flunet_dictionary.json')
    next(r for r in dictionary if r['FieldName']=='HUMAN_CORONA')['Description']='Number of coronavirus detections'
    with pytest.raises(ValueError,match='definition changed'):flu.detection_fields(dictionary)


def test_source_capture_date_controls_closed_weeks():
    raw=fixture('who_flunet_sample.json')[-1]
    with pytest.raises(ValueError,match='No reported closed-week'):flu_parse([raw],as_of=date(2026,10,23))
    rows,_=flu_parse([raw],as_of=date(2026,10,26))
    assert rows[0]['PeriodEnd']=='2026-10-25'


def test_annual_real_reported_counts_and_missing_are_separate():
    rows=annual_parse();bykey={(r['SourceDiseaseCode'],r['Year']):r for r in rows}
    assert bykey['TB_c_newinc','2025']['Cases']=='26672'
    assert bykey['WHS3_62','2025']['Cases']=='917'
    assert bykey['MALARIA_CONF_CASES','2024']['Cases']=='2384'
    assert bykey['CHOLERA_0000000001','2015']['Cases']=='244' # legacy Value string, NumericValue=null
    assert bykey['CHOLERA_0000000002','2015']['Measure']=='death_notifications'
    assert bykey['WHS3_53','2025']['Cases']=='' and bykey['WHS3_53','2025']['MissingValueStatus']=='source_missing'
    assert bykey['WHS3_55','2024']['Cases']=='0'
    assert bykey['WHS3_55','2025']['Cases']==''


@pytest.mark.parametrize('value', ['*','<5'])
def test_annual_suppression_in_numeric_or_text_slot(value):
    raw=fixture('who_gho_sample.json')[0]
    for numeric,text in [(value,None),(None,value)]:
        raw.update(NumericValue=numeric,Value=text)
        row=annual_parse([raw])[0]
        assert row['Cases']==value and row['MissingValueStatus']=='source_suppressed'


@pytest.mark.parametrize('change',[{'SpatialDim':'THA'},{'Dim1':'MLE','Dim1Type':'SEX'},{'NumericValue':1.1},{'Low':1},{'TimeDim':2026}])
def test_annual_estimates_strata_future_years_and_fractional_counts_fail(change):
    raw=fixture('who_gho_sample.json')[0];raw.update(change)
    with pytest.raises(ValueError):annual_parse([raw])


def test_annual_capture_year_is_stable_on_future_replay():
    raw=next(r for r in fixture('who_gho_sample.json') if r['TimeDim']==2025)
    with pytest.raises(ValueError,match='closed calendar'):annual_parse([raw],as_of=date(2025,12,31))
    assert annual_parse([raw],as_of=date(2026,1,1))[0]['Year']=='2025'


def test_updaters_refresh_full_history_despite_month_window_and_retain_policies():
    for updater,rows,start in [(MYWHOAnnualUpdater(),annual_parse(),1954),(MYWHOFluNetUpdater(),flu_parse()[0],1996)]:
        result=updater.prepare_rows(rows,months=[(2026,11)])
        assert len(result.rows)==len(rows)
        assert updater.full_history_start_year==start
        assert updater.series_quality_guard=={'mode':'fail_closed'}
        assert updater.mapping_notifications_enabled is updater.public_release_enabled is False


def artifact(tmp_path,body,*,url,dataset,index=0):
    payload=body if isinstance(body,bytes) else json.dumps(body).encode()
    path=tmp_path/'data/raw/who'/f'{dataset}-{index}.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path),sha256=hashlib.sha256(payload).hexdigest(),byte_size=len(payload),source_url=url,metadata_={'endpoint':'who_odata_page','dataset':dataset,'page_number':index,'requested_url':url,'retrieved_at':'2026-11-16T00:00:00Z'})


def test_replay_requires_complete_unique_page_chain_and_exact_counts(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);url='https://example.test/data?$filter=country';nextlink=url+'&$skip=1'
    first=artifact(tmp_path,{'value':[{'n':1}],'@odata.nextLink':nextlink,'@odata.count':2},url=url,dataset='a')
    second=artifact(tmp_path,{'value':[{'n':2}],'@odata.count':2},url=nextlink,dataset='a',index=1)
    with pytest.raises(ValueError,match='incomplete'):common.replay_pages([first],url=url,dataset='a')
    assert len(common.replay_pages([first,second],url=url,dataset='a'))==2
    with pytest.raises(ValueError,match='duplicate|unexpected'):common.replay_pages([first,first,second],url=url,dataset='a')
    with pytest.raises(ValueError,match='left'):common.next_url(url,'https://other.test/data',url)
    Path(second.relative_path).write_bytes(b'tampered')
    with pytest.raises(ValueError):common.replay_pages([first,second],url=url,dataset='a')


async def test_capture_collects_all_http_pages_and_detects_cycle():
    url='https://example.test/data';nxt=url+'?$skip=1';writer=SimpleNamespace(write=AsyncMock())
    class Crawler:
        def get(self,u):return SimpleNamespace(url=u,content=json.dumps({'value':[u],**({'@odata.nextLink':nxt} if u==url else {})}).encode())
    await common.capture_pages(writer,Crawler(),url=url,dataset='a',retrieved_at='2026-11-16')
    assert writer.write.await_count==2
    class Cycle:
        def get(self,u):return SimpleNamespace(url=u,content=json.dumps({'value':[],'@odata.nextLink':url}).encode())
    with pytest.raises(ValueError,match='cycle'):await common.capture_pages(writer,Cycle(),url=url,dataset='a',retrieved_at='2026-11-16')


def test_both_organizers_replay_raw_fixtures_without_network(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('offline organize used network'))
    flu_artifacts=[artifact(tmp_path,{'value':fixture('who_flunet_dictionary.json')},url=flu.DICTIONARY_URL,dataset='flunet_dictionary'),artifact(tmp_path,{'value':fixture('who_flunet_sample.json')},url=flu.DATA_URL,dataset='flunet_mys')]
    fetched=flu_connector.organize(flu_artifacts,updater=MYWHOFluNetUpdater())
    assert fetched.rows and all(r['RawSHA256'] and r['SourceDictionarySHA256'] for r in fetched.rows)
    gho=[artifact(tmp_path,{'value':fixture('who_gho_metadata.json')},url=annual.METADATA_URL,dataset='gho_indicators')]
    source=fixture('who_gho_sample.json')
    for code in annual.INDICATORS:gho.append(artifact(tmp_path,{'value':[r for r in source if r['IndicatorCode']==code]},url=annual.data_url(code),dataset=code))
    fetched=annual_connector.organize(gho,updater=MYWHOAnnualUpdater())
    assert fetched.source_audit['source_indicators_with_records']==15
    assert all(r['SourceIndicatorMetadataSHA256'] and r['SourceRecordId'] for r in fetched.rows)
    with pytest.raises(ValueError,match='Missing captured'):annual_connector.organize(gho[:-1],updater=MYWHOAnnualUpdater())


async def test_actual_store_keeps_two_laboratory_origins_without_collision(monkeypatch):
    raw=fixture('who_flunet_sample.json')[-1];other=copy.deepcopy(raw);other['ORIGIN_SOURCE']='NONSENTINEL'
    parsed=flu_parse([raw,other])[0];rows=[r for r in parsed if r['SourceDiseaseCode']=='INF_ALL']
    category=SimpleNamespace(id=1,category_key='who_flunet_inf_all',source_id=flu.ONTOLOGY_SOURCE_ID,source_code='INF_ALL',canonical_source_label=rows[0]['RawDiseaseLabel'],definition_version=rows[0]['DefinitionVersion'],source_definition=None,source_definition_uri=None)
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda:category)),add=lambda _:None,flush=AsyncMock())
    store=SeriesObservationStore();monkeypatch.setattr(store,'_resolve_series',lambda **kwargs:[]);monkeypatch.setattr(store,'_load_holding_series',AsyncMock(return_value=None))
    facts,handled=await store._build_holding_observations(db,rows,'MY',source_id=flu.ONTOLOGY_SOURCE_ID,value_field='Cases',geography_key='country:MY:national')
    assert handled==len(facts)==2
    assert len({(r['time'],r['series_code'],r['geography_key'],r['dimension_key']) for r in facts})==2
    assert facts[0]['metadata']['measure']=='laboratory_detections'
    assert facts[0]['raw_data']['SourceRSVSpecimensProcessed']==5


def test_annual_organizer_uses_capture_year_not_wall_clock(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    items=[artifact(tmp_path,{'value':fixture('who_gho_metadata.json')},url=annual.METADATA_URL,dataset='gho_indicators')]
    items[0].metadata_['retrieved_at']='2025-12-31T23:59:00Z'
    sample=fixture('who_gho_sample.json')
    for code in annual.INDICATORS:items.append(artifact(tmp_path,{'value':[r for r in sample if r['IndicatorCode']==code]},url=annual.data_url(code),dataset=code))
    with pytest.raises(ValueError,match='closed calendar'):annual_connector.organize(items,updater=MYWHOAnnualUpdater())
