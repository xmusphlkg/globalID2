"""Authentic NICD originals, reporting contracts and bounded live refresh."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.za import INDEX_URL, MEDIA_URLS, parse_report
from src.data.crawlers.za_cache import NICDReportCache
from src.data.processors.za import SouthAfricaNICDNMCUpdater
from src.services.ingestion.countries import za

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/ingestion/ZA'
PROVENANCE = json.loads((FIXTURES / 'provenance.json').read_text())

@pytest.fixture(scope='module')
def reports():
    return {name: parse_report((FIXTURES / name).read_bytes(), item['report']) for name,item in PROVENANCE.items()}

def matching(rows, disease, classification, geography='national', measure='cases', period='reported_month', cohort='all_notifications'):
    return [r for r in rows if disease.casefold() in r['RawDiseaseLabel'].casefold()
            and json.loads(r['Dimensions'])['case_classification']==classification
            and json.loads(r['Dimensions'])['cohort']==cohort
            and r['GeographyKey']==f'country:ZA:{geography}' and r['Measure']==measure and r['PeriodType']==period]

def fact(*args, **kwargs):
    values=matching(*args, **kwargs)
    assert len(values)==1, values
    return values[0]

def test_fixture_originals_have_verifiable_provenance():
    for name,item in PROVENANCE.items():
        assert hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest()==item['excerpt_sha256']
        assert item['url'].startswith('https://www.nicd.ac.za/')
        assert item['original_page_numbers']

def test_legacy_current_month_and_explicit_comparison_stay_distinct(reports):
    rows,_=reports['monthly_2023_03.pdf']
    assert fact(rows,'Cholera','all_notified')['Cases']=='5'
    assert fact(rows,'Cholera','patient_status_deceased',measure='deaths')['Cases']=='0'
    prior=fact(rows,'Cholera','all_notified',period='comparison_notification_month')
    assert (prior['Cases'],prior['Date'])==('6','2023-02-01')
    assert fact(rows,'Malaria','all_notified')['Cases']=='504'

def test_vital_status_is_not_an_invented_national_all_notification_total(reports):
    rows,_=reports['monthly_2023_05.pdf']
    assert fact(rows,'Cholera','all_notified',geography='province:GP')['Cases']=='139'
    assert fact(rows,'Cholera','patient_status_alive')['Cases']=='162'
    assert fact(rows,'Cholera','patient_status_deceased',measure='deaths')['Cases']=='4'
    assert not matching(rows,'Cholera','all_notified')

def test_suspected_and_probable_source_footnote_is_preserved(reports):
    rows,_=reports['monthly_2023_11.pdf']
    assert fact(rows,'Measles','suspected_or_probable')['Cases']=='492'
    assert fact(rows,'Measles','confirmed')['Cases']=='47'
    assert not matching(rows,'Measles','suspected')
    tb=fact(rows,'extensively','suspected')
    assert 'drug_resistant_xdr_tb' in tb['SourceDiseaseCode']

def test_classification_column_order_change_and_tb_blanks_remain_unknown(reports):
    old,_=reports['monthly_2024_01.pdf'];new,_=reports['monthly_2025_03.pdf']
    assert fact(old,'Measles','suspected')['Cases']=='177'
    assert fact(old,'Measles','confirmed')['Cases']=='5'
    assert fact(new,'Measles','confirmed')['Cases']=='19'
    assert fact(new,'Measles','suspected')['Cases']=='132'
    zero=fact(new,'extensively','confirmed');unknown=fact(new,'extensively','suspected')
    assert (zero['Cases'],zero['MissingValueStatus'])==('0','')
    assert (unknown['Cases'],unknown['MissingValueStatus'])==('','source_missing')
    assert zero['SourceDiseaseCode'].startswith('nmc2:')

def test_quarter_stays_quarter_and_inconsistent_status_group_is_withheld(reports):
    rows,audit=reports['quarter_2026_q1.pdf']
    assert {r['Frequency'] for r in rows}=={'quarterly'}
    assert {r['SourcePeriodStart'] for r in rows}=={'2026-01-01'}
    assert {r['SourcePeriodEnd'] for r in rows}=={'2026-03-31'}
    assert fact(rows,'Malaria','all_notified',period='reported_quarter')['Cases']=='2557'
    assert not matching(rows,'Diphtheria','patient_status_alive',period='reported_quarter',cohort='classification_table')
    assert any('do not reconcile' in issue['error'] for issue in audit['issues'])
    missing=fact(rows,'Waterborne','confirmed',period='reported_quarter',cohort='classification_table')
    assert (missing['Cases'],missing['MissingValueStatus'])==('','source_missing')

def test_modern_national_province_and_classification_are_independent(reports):
    rows,_=reports['monthly_2026_08.pdf']
    assert fact(rows,'Agricultural','all_notified',geography='province:GP')['Cases']=='61'
    assert fact(rows,'Agricultural','all_notified')['Cases']=='120'
    assert fact(rows,'Agricultural','patient_status_deceased',measure='deaths',cohort='classification_table')['Cases']=='25'
    assert fact(rows,'Measles','confirmed',cohort='classification_table')['Cases']=='212'
    assert fact(rows,'Measles','all_notified')['Cases']=='1847'
    # Named month can use source ISO weeks. Do not assert calendar bounds.
    assert {r['SourcePeriodStart'] for r in rows}=={''}
    assert {r['SourcePeriodEnd'] for r in rows}=={''}
    assert any('ISO' in r['SourceWindowDetail'] for r in rows)

def test_current_archive_edition_wins_but_equal_rank_conflict_is_withheld(monkeypatch):
    base={'Date':'2024-12-01','SourceDiseaseCode':'nmc1:test','GeographyKey':'country:ZA:national','DefinitionVersion':'v1','SourceReportYear':'2024','SourceReportMonth':'12','SourceReportCurrentLink':'false','SourceReportPublicationDate':'2025-01-01','SourceURL':'old','Cases':'10'}
    current=base|{'SourceReportCurrentLink':'true','SourceURL':'current','Cases':'12'}
    monkeypatch.setattr(za,'parse_report',lambda payload,report: ([report['row']],{}))
    rows,audit=za.organize_documents([({'url':'old','row':base},b''),({'url':'current','row':current},b'')])
    assert [r['Cases'] for r in rows]==['12']
    other=current|{'SourceURL':'other','Cases':'13'}
    rows,audit=za.organize_documents([({'url':'current','row':current},b''),({'url':'other','row':other},b'')])
    assert not rows and len(audit['conflicting_observations'])==1

class Transport:
    def __init__(self,responses): self.responses=iter(responses);self.calls=[]
    def get(self,url,**kwargs): self.calls.append((url,kwargs));return next(self.responses)

def response(payload=b'%PDF original',status=200,headers=None):
    return SimpleNamespace(content=payload,status_code=status,headers=headers or {},url='https://www.nicd.ac.za/report.pdf')

def test_cache_ttl_304_and_changed_source_preserve_immutable_versions(tmp_path):
    now=datetime(2026,11,16,tzinfo=timezone.utc);url='https://www.nicd.ac.za/report.pdf'
    transport=Transport([response(headers={'ETag':'v1'}),response(status=304),response(b'%PDF revision',headers={'ETag':'v2'})]);cache=NICDReportCache(tmp_path)
    first,r1=cache.get(transport,url,now=now)
    hit,r2=cache.get(transport,url,now=now+timedelta(hours=1))
    assert hit==first and r2['cache_status']=='hit_verified' and len(transport.calls)==1
    unchanged,r3=cache.get(transport,url,now=now+timedelta(hours=25))
    assert unchanged==first and r3['cache_status']=='revalidated_not_modified'
    assert r3['retrieved_at']==r1['retrieved_at']
    assert transport.calls[1][1]['headers']=={'If-None-Match':'v1'}
    changed,r4=cache.get(transport,url,force_refresh=True,now=now+timedelta(hours=26))
    assert changed!=first and r4['sha256']!=r1['sha256']
    assert Path(r1['path']).read_bytes()==first
    assert Path(r4['path']).read_bytes()==changed

def test_cache_integrity_failure_does_not_silently_refetch(tmp_path):
    transport=Transport([response()]);cache=NICDReportCache(tmp_path)
    _,receipt=cache.get(transport,'https://www.nicd.ac.za/report.pdf')
    Path(receipt['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError,match='integrity'):
        cache.get(transport,'https://www.nicd.ac.za/report.pdf',force_refresh=True)
    assert len(transport.calls)==1

@pytest.mark.asyncio
async def test_live_discovery_every_run_paginates_and_retains_ambiguous_year(monkeypatch):
    old='https://www.nicd.ac.za/old.pdf';recent='https://www.nicd.ac.za/recent.pdf';new='https://www.nicd.ac.za/wp-content/uploads/NMCSS_report_June_unknown.pdf'
    calls=[]
    class Crawler:
        session=SimpleNamespace(close=lambda:None)
        def get(self,url,**kwargs):
            calls.append(url)
            if url==INDEX_URL: payload=f'<a href="{old}">NMC REPORT 2023</a><a href="{recent}">NMC REPORT 2026</a>'.encode();headers={}
            elif url==MEDIA_URLS[0]:payload=b'[]';headers={'X-WP-TotalPages':'2'}
            elif '&page=2' in url:payload=json.dumps([{'id':42,'source_url':new,'title':{'rendered':'NMC monthly report'}}]).encode();headers={}
            else:payload=b'[]';headers={}
            return SimpleNamespace(content=payload,headers=headers,url=url)
    downloaded=[]
    class Cache:
        def get(self,crawler,url,**kwargs):
            downloaded.append((url,kwargs))
            return b'%PDF original',{'retrieved_at':'now','revalidated_at':'now','cache_status':'hit_verified'}
    class Writer:
        def __init__(self):self.artifacts=[]
        async def write(self,payload,**kwargs):self.artifacts.append(SimpleNamespace(payload=payload,**kwargs))
    monkeypatch.setattr(za,'SouthAfricaNICDNMCCrawler',Crawler)
    monkeypatch.setattr('src.data.crawlers.za_cache.NICDReportCache',Cache)
    for _ in range(2):
        writer=Writer();await za.collect(writer,start_year=2025,cache_ttl_hours=12,force_refresh=True)
        plan=json.loads(next(a.payload for a in writer.artifacts if a.kind=='za_nicd_plan'))
        assert plan['catalogue_discovered_reports']==3
        assert {r['url'] for r in plan['reports']}=={recent,new}
    assert calls.count(INDEX_URL)==2 and calls.count(MEDIA_URLS[0]+'&page=2')==2
    assert all(options=={'ttl_hours':12,'force_refresh':True} for _,options in downloaded)


def test_offline_organization_rejects_corrupted_durable_original(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path);path=Path('data/raw/za/report.pdf');path.parent.mkdir(parents=True);path.write_bytes(b'%PDF modified')
    plan_path=path.with_suffix('.json');payload=json.dumps({'reports':[]}).encode();plan_path.write_bytes(payload)
    def artifact(p,kind,original,metadata=None):
        return SimpleNamespace(kind=kind,relative_path=str(p),sha256=hashlib.sha256(original).hexdigest(),byte_size=len(original),metadata_=metadata or {})
    artifacts=[artifact(plan_path,'za_nicd_plan',payload),artifact(path,'za_nicd_report',b'%PDF original',{'report':{'url':'original'}})]
    with pytest.raises(ValueError,match='integrity'):
        za.organize(artifacts,updater=SouthAfricaNICDNMCUpdater())

def test_asymptomatic_carrier_column_is_never_shifted_into_probable(reports):
    rows,_=reports['monthly_2026_07.pdf']
    assert fact(rows,'Diphtheria','asymptomatic_carrier',cohort='classification_table')['Cases']=='3'
    assert fact(rows,'Diphtheria','probable',cohort='classification_table')['Cases']=='2'
    assert fact(rows,'Diphtheria','suspected',cohort='classification_table')['Cases']=='13'


def test_real_alternate_docx_keeps_explicit_combined_case_definition(reports):
    rows,_=reports['monthly_2023_10.docx']
    assert fact(rows,'Cholera','suspected_or_probable')['Cases']=='3'
    assert fact(rows,'Cholera','confirmed')['Cases']=='0'
    assert fact(rows,'Agricultural','all_notified',geography='province:GP')['Cases']=='78'


def test_inconsistent_province_group_does_not_erase_printed_national_total(reports):
    rows,audit=reports['monthly_2026_08.pdf']
    assert fact(rows,'Shigellosis','all_notified')['Cases']=='49'
    assert not matching(rows,'Shigellosis','all_notified',geography='province:GP')
    assert any('Province sum 46' in issue['error'] for issue in audit['issues'])


def test_recent_window_filters_by_verified_report_values(reports):
    old,_=reports['monthly_2023_03.pdf'];recent,_=reports['monthly_2026_08.pdf']
    fetched=SouthAfricaNICDNMCUpdater().prepare_rows(old+recent,start_year=2025)
    assert fetched.rows and {r['Year'] for r in fetched.rows}=={'2026'}


def test_missing_phantom_grid_cell_cannot_shift_province_into_national_count(reports):
    rows,audit=reports['monthly_2025_06.pdf']
    assert not matching(rows,'Haemophilus','all_notified')
    assert not matching(rows,'Legionellosis','all_notified')
    assert any('positional mapping withheld' in issue['error'] for issue in audit['issues'])
