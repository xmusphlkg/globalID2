"""Official Indonesian profile fixtures: national counts and source contracts."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers.id import (
    CHANNEL_URL, IndonesiaContractError, discover_profiles, discover_downloads,
    integer, national_cells, native_row, parse_national_narratives, parse_profile_pdf,
)
from src.data.processors.id import IndonesiaKemkesProfileUpdater
from src.services.ingestion.countries import id as connector

FIXTURES=Path(__file__).resolve().parents[1]/'fixtures/ingestion/ID'
REFERENCE={'year':2024,'page_url':'https://kemkes.go.id/id/profil-kesehatan-indonesia-2024',
 'url':'https://drive.google.com/file/d/1-lNRA3k9o9jM5vGacbnKY4OZorUQ-_Sc/view?usp=sharing',
 'download_kind':'linked_google_drive','title':'Profil Kesehatan Indonesia 2024','published_date':None}


@pytest.fixture(scope='module')
def parsed():
    return parse_profile_pdf((FIXTURES/'profile2024_selected_pages.pdf').read_bytes(),reference=REFERENCE)


def test_live_directory_is_not_capped_at_an_implementation_end_year():
    payload=(FIXTURES/'index.html').read_bytes()
    targets=discover_profiles(payload)
    assert [r['year'] for r in targets]==list(range(2017,2026))
    assert [r['year'] for r in discover_profiles(payload,start_year=2024)]==[2024,2025]
    assert [r['year'] for r in discover_profiles(payload,start_year=2020,end_year=2021)]==[2020,2021]
    updated=payload+b'<a href="https://kemkes.go.id/id/profil-kesehatan-indonesia-2026">2026</a>'
    assert discover_profiles(updated,start_year=2026)[0]['year']==2026


def test_actual_malformed_legacy_annual_page_preserves_linked_google_drive():
    target=next(t for t in discover_profiles((FIXTURES/'index.html').read_bytes()) if t['year']==2024)
    links=discover_downloads((FIXTURES/'profile_2024.html').read_bytes(),reference=target)
    assert len(links)==1 and links[0]['url']==REFERENCE['url']
    with pytest.raises(IndonesiaContractError,match='title'):
        discover_downloads((FIXTURES/'profile_2024.html').read_bytes(),reference=dict(target,year=2023))


def test_real_national_dengue_cases_and_deaths_remain_different_measures(parsed):
    rows,audit=parsed; by_code={r['SourceDiseaseCode']:r for r in rows}
    assert by_code['dbd__cases']['Cases']=='257271'
    assert by_code['dbd__deaths']['Cases']=='1461'
    assert by_code['dbd__deaths']['Measure']=='deaths'
    assert by_code['hiv__cases']['Cases']=='63707'
    assert by_code['tetanus_neonatorum__cases']['Cases']=='43'
    assert by_code['campak__suspected_cases']['Cases']=='25639'
    assert by_code['campak__confirmed_cases']['Cases']=='3309'
    assert audit['status']=='partial'
    assert all(r['GeographyKey']=='country:ID:national' and r['PublicReleaseEnabled']=='false' for r in rows)


def table_rows(parsed,appendix):
    return [r for r in parsed[0] if json.loads(r['Dimensions']).get('source_appendix')==appendix]


def test_historical_hiv_header_years_remain_source_statistic_years(parsed):
    rows=table_rows(parsed,'59.b')
    assert {(r['Year'],r['Cases']) for r in rows}=={('2022','52955'),('2023','57299'),('2024','63707')}
    assert {r['SourceReportYear'] for r in rows}=={'2024'}
    assert all(r['PeriodStart']==r['Year']+'-01-01' for r in rows)


def test_monthly_cells_never_become_annual_and_other_count_contracts_are_excluded(parsed):
    monthly=table_rows(parsed,'69.c')
    assert len(monthly)==1 and monthly[0]['Cases']=='25639'
    assert json.loads(monthly[0]['Dimensions'])['source_column_header'].endswith('Total')
    assert not table_rows(parsed,'67')  # treatment cohorts, not annual incident cases
    assert not table_rows(parsed,'68')  # non-polio AFP, not polio diagnoses
    assert not table_rows(parsed,'69.g') # outbreak frequencies need a separate contract


def test_registered_stock_new_registrations_transfers_and_deaths_remain_distinct(parsed):
    rows=table_rows(parsed,'74.a')
    kinds={json.loads(r['Dimensions'])['count_kind']:r for r in rows}
    assert {k:r['Cases'] for k,r in kinds.items()}=={'newly_registered_cases':'66','transfers':'47','deaths':'519','registered_cases':'5601'}
    assert kinds['registered_cases']['PeriodType']=='point_in_time'
    assert kinds['registered_cases']['PeriodStart']==''
    assert kinds['newly_registered_cases']['PeriodType']=='annual'


def test_multilevel_headers_do_not_inherit_labels_across_parent_groups(parsed):
    rows=table_rows(parsed,'61')
    oralit=next(r for r in rows if r['Cases']=='2816958')
    header=json.loads(oralit['Dimensions'])['source_column_header']
    assert 'Semua Umur' in header and 'Balita' not in header


def test_physical_page_bounds_exclude_hidden_neighbouring_appendices(parsed):
    rows=table_rows(parsed,'69.a')
    assert len(rows)==14
    assert all(json.loads(r['Dimensions'])['source_appendix']=='69.a' for r in rows)
    provenance=json.loads((FIXTURES/'provenance.json').read_text())
    assert {provenance['fixture_to_source_physical_page'][r['SourcePage']] for r in rows}=={484}


def test_national_merged_row_alignment_is_strict_and_no_province_total_is_invented():
    table=[['37 Papua Pegunungan\nIndonesia\n38 Papua Barat Daya',None,'249\n257.271\n263','0\n1.461\n0']]
    assert national_cells(table)[2:]==['257.271','1.461']
    table[0][2]='249257.271263'
    with pytest.raises(IndonesiaContractError,match='overlaps'):national_cells(table)
    with pytest.raises(IndonesiaContractError,match='national row'):national_cells([['Papua','10']])


@pytest.mark.parametrize('value',['1,2','12.34','I.234','1 234','-','...'])
def test_unreadable_and_decimal_counts_are_never_repaired(value):
    with pytest.raises(IndonesiaContractError):integer(value)


def test_explicit_zero_and_missing_are_different_and_local_narratives_are_rejected():
    base=dict(code='dbd',kind='cases',year=2024,reference=REFERENCE,digest='a'*64,page=1,evidence='source')
    assert native_row('DBD','0',**base)['MissingValueStatus']==''
    assert native_row('DBD','',**base)['MissingValueStatus']=='source_missing'
    assert integer('257.271')=='257271'
    rows,_=parse_national_narratives(['Indonesia. Pada tahun 2024 terdapat 123 kasus DBD di Provinsi Bali.'],reference=REFERENCE,digest='a'*64)
    assert rows==[]


def test_actual_official_2025_shortlink_and_public_filename_can_be_retried():
    share=connector.shortlink_target((FIXTURES/'profile2025_shortlink.json').read_bytes())
    assert share=='https://drive.kemkes.go.id:5001/sharing/xfxEZXL5u'
    url=connector.share_download((FIXTURES/'profile2025_share_session.js').read_bytes(),share_url=share)
    assert url=='https://drive.kemkes.go.id:5001/fsdownload/xfxEZXL5u/Profil%20Kesehatan%20Indonesia%202025-ok.pdf'
    with pytest.raises(IndonesiaContractError):connector.shortlink_target(b'{"is_public":false}')


class FixtureCrawler:
    def __init__(self):self.session=SimpleNamespace(close=lambda:None)
    def get(self,url):
        files={CHANNEL_URL:'index.html',REFERENCE['page_url']:'profile_2024.html',
          'https://drive.google.com/uc?export=download&id=1-lNRA3k9o9jM5vGacbnKY4OZorUQ-_Sc':'profile2024_selected_pages.pdf'}
        assert url in files, f'Unexpected source request {url}'
        response=requests.Response();response.status_code=200;response.url=url
        response._content=(FIXTURES/files[url]).read_bytes();return response


class FixtureWriter:
    def __init__(self,path):self.path=path;self.artifacts=[]
    async def write(self,payload,*,source_url,kind,metadata=None):
        digest=hashlib.sha256(payload).hexdigest();path=self.path/(digest+'.bin');path.write_bytes(payload)
        self.artifacts.append(SimpleNamespace(relative_path=str(path),sha256=digest,byte_size=len(payload),
           source_url=source_url,kind=kind,metadata_=metadata or {}))


def test_collect_then_offline_organize_checks_source_plan_and_manifest(tmp_path,monkeypatch):
    writer=FixtureWriter(tmp_path)
    asyncio.run(connector.collect(writer,start_year=2024,end_year=2024,crawler_type=FixtureCrawler))
    def read(artifact):
        payload=Path(artifact.relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest()==artifact.sha256
        assert len(payload)==artifact.byte_size
        return payload
    monkeypatch.setattr(connector,'read_artifact',read)
    monkeypatch.setattr(FixtureCrawler,'get',lambda *a,**kw:pytest.fail('Offline organization made a request'))
    result=connector.organize(writer.artifacts,updater=IndonesiaKemkesProfileUpdater(),start_year=2024,end_year=2024)
    assert result.source_audit['captured_profiles']==1
    assert any(r['SourceDiseaseCode']=='dbd__cases' and r['Cases']=='257271' for r in result.rows)
    missing=[a for a in writer.artifacts if a.metadata_.get('endpoint')!='profile_pdf']
    with pytest.raises(IndonesiaContractError,match='manifest'):
        connector.organize(missing,updater=IndonesiaKemkesProfileUpdater(),start_year=2024,end_year=2024)
    with pytest.raises(IndonesiaContractError,match='window'):
        connector.organize(writer.artifacts,updater=IndonesiaKemkesProfileUpdater(),start_year=2023,end_year=2024)


def test_raw_persistence_failure_is_not_downgraded_to_source_unavailability(tmp_path):
    class FailingWriter(FixtureWriter):
        async def write(self,*args,**kwargs):
            if kwargs['metadata']['endpoint']=='profile_pdf':raise ValueError('disk persistence failed')
            return await super().write(*args,**kwargs)
    with pytest.raises(ValueError,match='persistence'):
        asyncio.run(connector.collect(FailingWriter(tmp_path),start_year=2024,end_year=2024,crawler_type=FixtureCrawler))

@pytest.mark.parametrize('year,tb,kusta',[(2017,'425089','15910'),(2018,'566623','17017')])
def test_contents_page_lampiran_does_not_hide_early_national_disease_chapters(year,tb,kusta):
    reference={'year':year,'url':f'https://kemkes.go.id/id/profil-kesehatan-indonesia-{year}',
        'page_url':f'https://kemkes.go.id/id/profil-kesehatan-indonesia-{year}'}
    rows,_=parse_profile_pdf((FIXTURES/f'profile{year}_selected_pages.pdf').read_bytes(),reference=reference)
    values={r['SourceDiseaseCode']:r['Cases'] for r in rows}
    assert values['tuberkulosis__cases']==tb
    assert values['kusta__cases']==kusta


def test_real_2022_case_tests_targets_and_cumulative_schemas_remain_separate():
    reference={'year':2022,'url':'https://kemkes.go.id/id/profil-kesehatan-indonesia-2022',
        'page_url':'https://kemkes.go.id/id/profil-kesehatan-indonesia-2022'}
    rows,_=parse_profile_pdf((FIXTURES/'profile2022_selected_pages.pdf').read_bytes(),reference=reference)
    dimensions=[json.loads(r['Dimensions']) for r in rows]
    # Physical table 73.a distinguishes malaria-positive cases from the tests
    # performed to ascertain them; confirmation is not itself a case count.
    malaria=[r for r,d in zip(rows,dimensions) if d.get('source_appendix')=='73.a']
    tests=[r for r in malaria if r['Measure']=='laboratory_tests']
    assert {r['Cases'] for r in tests}=={'1745479','1612968','3358447'}
    assert any(r['Cases']=='443530' and r['Measure']=='confirmed_case_notifications' for r in malaria)
    # Hospital beds, child population, planning targets, treatment cohorts and
    # COVID cumulative tables never become annual incident case observations.
    assert not any(d.get('source_appendix') in {'57','58','84.a','85'} for d in dimensions)
    assert not any('target' in d.get('source_column_header','').lower() for d in dimensions)
    assert not any(d.get('source_appendix')=='74.d' for d in dimensions)
    confirmed=[r for r,d in zip(rows,dimensions) if d.get('source_appendix')=='69.e' and r['Cases']=='4844']
    assert len(confirmed)==1 and confirmed[0]['Measure']=='confirmed_case_notifications'


def test_cumulative_aids_and_hiv_service_denominators_are_not_annual_cases():
    reference={'year':2020,'url':'https://kemkes.go.id/id/profil-kesehatan-indonesia-2020',
        'page_url':'https://kemkes.go.id/id/profil-kesehatan-indonesia-2020'}
    rows,_=parse_profile_pdf((FIXTURES/'profile2020_selected_pages.pdf').read_bytes(),reference=reference)
    cumulative=[r for r in rows if json.loads(r['Dimensions']).get('source_appendix')=='55.c']
    assert cumulative and all(r['Measure']=='cumulative_case_notifications' for r in cumulative)
    assert all(r['PeriodType']=='cumulative' and r['PeriodStart']=='' for r in cumulative)
    assert not any('pelayanan' in json.loads(r['Dimensions']).get('source_column_header','').lower() for r in rows)
