"""Exact real official DGE PDFs across changing layouts, source years and strata."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data.crawlers.mx import (MexicoDGEContractError,discover_national_tables,parse_national_pdf,_number,DEFAULT_SCOPE,NATIONAL_URL,INCIDENCE_URL)
from src.data.processors.mx import MexicoDGEAnnualUpdater
from src.services.ingestion.countries import mx

FIXTURES=Path(__file__).resolve().parents[1]/'fixtures/ingestion/MX'

@pytest.fixture(scope='module')
def references():
    return discover_national_tables((FIXTURES/'national_index.html').read_bytes(),incidence_payload=(FIXTURES/'incidence_index.html').read_bytes())

def ref(references,year,*,sex='all',table='national_cases_age'):
    return next(r for r in references if r['year']==year and r['sex']==sex and r['table']==table)

def parse(references,year,*,sex='all',table='national_cases_age'):
    return parse_national_pdf((FIXTURES/f'{year}_{sex}_{table}.pdf').read_bytes(),reference=ref(references,year,sex=sex,table=table))

def cell(rows,label,age='all'):
    return next(r for r in rows if label in r['RawDiseaseLabel'] and r['AgeGroup']==age)

def test_real_menus_discover_all_years_and_legacy_sex_paths(references):
    assert len(references)==176
    assert {r['year'] for r in references}==set(range(1984,2026))
    assert ref(references,2003,sex='female')['edition_path']=='5000'
    assert ref(references,2012,sex='male')['edition_path']=='1401'
    assert ref(references,2025,sex='female')['edition_path']=='20250'
    count2002=ref(references,2002,table='national_cases_institution')
    assert '/2002/morbilidad/nacional/' in count2002['url']
    assert not any('/estatal/' in r['url'] for r in references)

@pytest.mark.parametrize('year,first_value',[(1984,'851485'),(1992,'3074'),(1997,'8560'),(1999,'6100'),(2003,'67051'),(2004,'73636'),(2025,'598')])
def test_real_national_count_headers_and_thousands(year,first_value,references):
    rows,audit=parse(references,year)
    assert rows[0]['Cases']==first_value
    assert rows[0]['AgeGroup']=='all'
    assert {r['GeographyKey'] for r in rows}=={'country:MX:national'}
    assert {r['Measure'] for r in rows}=={'case_notifications'}
    assert all(r['Date']==f'{year}-01-01' and r['PeriodEnd']==f'{year}-12-31' for r in rows)
    assert audit['status'] in {'parsed','partial'}

def test_2025_explicit_zero_and_not_applicable_are_distinct(references):
    rows,_=parse(references,2025)
    assert cell(rows,'Cólera')['Cases']=='0'
    assert cell(rows,'Cólera','lt1')['Cases']=='0'
    na=cell(rows,'Anencefalia','1to4')
    assert na['Cases']=='' and na['MissingValueStatus']=='not_applicable'
    assert cell(rows,'Anencefalia')['Cases']=='168'
    assert not any(r['RawDiseaseLabel'].casefold()=='total' for r in rows)
    assert all(r['PublicReleaseEnabled']=='false' for r in rows)

def test_rates_and_counts_never_share_source_category_identity(references):
    counts,_=parse(references,2025)
    rates,_=parse(references,2025,table='national_incidence_age')
    count=cell(counts,'Absceso hepático amebiano');rate=cell(rates,'Absceso hepático amebiano')
    assert count['Cases']=='598' and rate['Cases']=='0.45'
    assert rate['Unit']=='per_100000' and rate['Measure']=='incidence_rate'
    assert count['SourceDiseaseCode']!=rate['SourceDiseaseCode']
    assert 'rate_denominator' in json.loads(rate['Dimensions'])


def test_source_female_pdf_with_general_following_pages_is_rejected(references):
    rows,audit=parse(references,2025,sex='female',table='national_incidence_age')
    assert rows and audit['status']=='partial'
    assert {json.loads(r['Dimensions'])['source_page'] for r in rows}=={1}
    assert all('Female edition' in failure['error'] for failure in audit['failures'])


def test_1984_age_and_reporting_institutions_are_not_added_together(references):
    rows,_=parse(references,1984)
    national=cell(rows,'AMIBIASIS')
    ages=[r for r in rows if r['RawDiseaseLabel']=='AMIBIASIS' and r['AgeGroup']!='all']
    assert len(ages)==7
    assert sum(int(r['Cases']) for r in ages)==int(national['Cases'])
    assert not any(r['AgeGroup'] in {'SSA','IMSS','ISSSTE','OTROS'} for r in rows)


def test_2004_numerical_columns_are_learned_from_data_not_shifted_header(references):
    rows,_=parse(references,2004)
    total=cell(rows,'Enfermedades cerebrovasculares')
    ages=[r for r in rows if r['SourceICDCode']==total['SourceICDCode'] and r['AgeGroup']!='all']
    assert total['Cases']=='36804'
    assert sum(int(r['Cases']) for r in ages if r['Cases'])==36804


def test_2010_first_age_heading_on_adjacent_baseline_is_retained(references):
    rows,_=parse(references,2010)
    total=cell(rows,'Amebiasis intestinal')
    ages=[r for r in rows if r['SourceDiseaseCode'].split('__')[0]==total['SourceDiseaseCode'].split('__')[0] and r['AgeGroup']!='all']
    assert total['Cases']=='462767'
    assert cell(rows,'Amebiasis intestinal','lt1')['Cases']=='19801'
    assert sum(int(r['Cases']) for r in ages if r['Cases'])==462767

@pytest.mark.parametrize('raw,rate,value',[('3 041,73',True,'3041.73'),('13,007.72',True,'13007.72'),('255 377',False,'255377'),('0',False,'0'),('N.A',False,'')])
def test_source_numeric_formats(raw,rate,value):assert _number(raw,rate=rate)[0]==value

@pytest.mark.parametrize('raw,rate',[('321 949 1 090 886',False),('31,247.07 22,990.95',True),('-1',False),('4.5',False)])
def test_merged_adjacent_cells_and_invalid_counts_fail_closed(raw,rate):
    with pytest.raises(MexicoDGEContractError):_number(raw,rate=rate)


def _artifact(body,kind,url,metadata=None):
    return SimpleNamespace(kind=kind,source_url=url,sha256=hashlib.sha256(body).hexdigest(),byte_size=len(body),metadata_=metadata or {},body=body)


def test_organize_is_offline_and_requires_exact_frozen_capture_manifest(references,monkeypatch):
    reference=ref(references,2025)
    body=(FIXTURES/'2025_all_national_cases_age.pdf').read_bytes()
    pdf=_artifact(body,'mx_dge_pdf',reference['url'])
    refs=[r for r in references if r['year']==2025]
    plan={'references':refs,'captured':[{'url':pdf.source_url,'sha256':pdf.sha256}],'errors':[{'url':r['url'],'stage':'download','error':'fixture intentionally not captured'} for r in refs if r['url']!=pdf.source_url],'requested_start_year':2025}
    frozen=_artifact(json.dumps(plan).encode(),'mx_dge_plan',NATIONAL_URL)
    indexes=[_artifact((FIXTURES/'national_index.html').read_bytes(),'mx_dge_index',NATIONAL_URL),_artifact((FIXTURES/'incidence_index.html').read_bytes(),'mx_dge_index',INCIDENCE_URL)]
    monkeypatch.setattr(mx,'read_artifact',lambda a:a.body)
    monkeypatch.setattr(mx.MexicoDGEAnnualCrawler,'get',lambda *a,**k:pytest.fail('organization requested live source'))
    result=mx.organize([pdf,frozen,*indexes],updater=MexicoDGEAnnualUpdater())
    assert result.source_latest_date==date(2025,1,1)
    assert len(result.rows)>2000
    assert 'failed_count' in result.source_audit
    with pytest.raises(MexicoDGEContractError,match='manifest'):
        mx.organize([frozen,*indexes],updater=MexicoDGEAnnualUpdater())


def test_annual_rows_ignore_monthly_windows(references):
    rows,_=parse(references,2025)
    updater=MexicoDGEAnnualUpdater()
    result=updater.prepare_rows(rows,start_year=2025,months={(2025,11)})
    assert len(result.rows)==len(rows)


def test_real_fixtures_have_source_receipts_and_exact_sha256():
    for item in json.loads((FIXTURES/'provenance.json').read_text()):
        body=(FIXTURES/item['fixture']).read_bytes()
        assert hashlib.sha256(body).hexdigest()==item['sha256']
        assert len(body)==item['byte_size']
        assert item['source_url'].startswith('https://epidemiologia.salud.gob.mx/anuario/')


@pytest.mark.parametrize('cache_ttl_days',[None,0.5,2])
def test_staged_collect_persists_cached_pdf_bytes_and_refreshes_live_directory(references,monkeypatch,cache_ttl_days):
    import asyncio
    body=(FIXTURES/'2025_all_national_cases_age.pdf').read_bytes();reference=ref(references,2025);calls=[]
    def discover(self,**options):calls.append(('live_discovery',options));return [reference],[(NATIONAL_URL,b'index'),(INCIDENCE_URL,b'index')]
    def download(self,reference,**options):calls.append(('download',options));return body,{'retrieved_at':'2026-11-16T00:00:00+00:00','cache_reused':True}
    monkeypatch.setattr(mx.MexicoDGEAnnualCrawler,'discover',discover)
    monkeypatch.setattr(mx.MexicoDGEAnnualCrawler,'download',download)
    class Writer:
        def __init__(self):self.artifacts=[]
        async def write(self,payload,*,source_url,kind,metadata=None):
            item=_artifact(payload,kind,source_url,metadata);self.artifacts.append(item);return item
    options={} if cache_ttl_days is None else {'cache_ttl_days':cache_ttl_days}
    writer=Writer();artifacts=asyncio.run(mx.collect(writer,start_year=2023,**options))
    pdf=next(a for a in artifacts if a.kind=='mx_dge_pdf')
    assert pdf.body==body and pdf.metadata_['cache_reused'] is True
    assert calls[0]==('live_discovery',{'start_year':2023,'end_year':None})
    assert calls[1]==('download',{'force':False,'cache_ttl_days':1 if cache_ttl_days is None else cache_ttl_days})
    plan=json.loads(next(a for a in artifacts if a.kind=='mx_dge_plan').body)
    assert plan['captured']==[{'url':pdf.source_url,'sha256':pdf.sha256}]


@pytest.mark.parametrize('cache_ttl_days,refresh',[(None,True),(2,False)])
def test_staged_daily_cache_rechecks_official_pdf_after_one_day(references,tmp_path,monkeypatch,cache_ttl_days,refresh):
    import asyncio
    from datetime import datetime,timezone,timedelta
    body=(FIXTURES/'2025_all_national_cases_age.pdf').read_bytes()
    reference=ref(references,2025)
    monkeypatch.chdir(tmp_path)
    calls=[]
    monkeypatch.setattr(mx.MexicoDGEAnnualCrawler,'get',lambda self,url:(calls.append(url) or SimpleNamespace(content=body)))
    # Prime the real immutable byte cache, then age its actual receipt to a
    # point that must be refreshed daily but is still fresh for a 2-day replay.
    crawler=mx.MexicoDGEAnnualCrawler()
    try:crawler.download(reference)
    finally:crawler.session.close()
    receipt=next(Path('data/raw/mx/dge_annual_cache').glob('*.json'))
    metadata=json.loads(receipt.read_text())
    metadata['retrieved_at']=(datetime.now(timezone.utc)-timedelta(hours=36)).isoformat()
    receipt.write_text(json.dumps(metadata))
    monkeypatch.setattr(mx.MexicoDGEAnnualCrawler,'discover',lambda self,**options:([reference],[(NATIONAL_URL,b'index'),(INCIDENCE_URL,b'index')]))
    class Writer:
        def __init__(self):self.artifacts=[]
        async def write(self,payload,*,source_url,kind,metadata=None):
            item=_artifact(payload,kind,source_url,metadata);self.artifacts.append(item);return item
    writer=Writer()
    options={} if cache_ttl_days is None else {'cache_ttl_days':cache_ttl_days}
    artifacts=asyncio.run(mx.collect(writer,start_year=2025,**options))
    pdf=next(a for a in artifacts if a.kind=='mx_dge_pdf')
    assert calls==[reference['url']]*(2 if refresh else 1)
    assert pdf.body==body and pdf.metadata_['cache_reused']==(not refresh)
    assert len(artifacts)==4  # Both menus, original PDF, and collection plan.
    assert (pdf.metadata_['retrieved_at']!=metadata['retrieved_at'])==refresh


def test_actual_crawler_cache_checks_sha_age_and_does_not_fallback_after_refresh_error(references,tmp_path,monkeypatch):
    from datetime import datetime,timezone,timedelta
    from src.data.crawlers.mx import MexicoDGEAnnualCrawler
    body=(FIXTURES/'2025_all_national_cases_age.pdf').read_bytes();reference=ref(references,2025)
    monkeypatch.chdir(tmp_path);crawler=MexicoDGEAnnualCrawler();calls=[]
    monkeypatch.setattr(crawler,'get',lambda url:(calls.append(url) or SimpleNamespace(content=body)))
    first,metadata=crawler.download(reference);second,cached=crawler.download(reference)
    assert first==second==body and len(calls)==1 and cached['cache_reused'] is True
    receipt=next(Path('data/raw/mx/dge_annual_cache').glob('*.json'))
    old=json.loads(receipt.read_text());old['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat();receipt.write_text(json.dumps(old))
    def unavailable(url):raise RuntimeError('official refresh unavailable')
    monkeypatch.setattr(crawler,'get',unavailable)
    with pytest.raises(RuntimeError,match='refresh unavailable'):crawler.download(reference)
    old['retrieved_at']=datetime.now(timezone.utc).isoformat();receipt.write_text(json.dumps(old))
    Path(old['relative_path']).write_bytes(b'%PDF corrupted')
    with pytest.raises(MexicoDGEContractError,match='checksum'):crawler.download(reference)
    crawler.session.close()
