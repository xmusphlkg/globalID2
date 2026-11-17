"""Real official BEN page extracts exercise period and source-column contracts."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data.crawlers.ar import (
    ArgentinaBENContractError, parse_ben_payload, parse_count, parse_eno_table,
    source_report_identity, epidemiological_week_end, links_from_html,
    deduplicate_revisions,
)
from src.data.processors.ar import ArgentinaBENUpdater

FIXTURES=Path(__file__).resolve().parents[1]/'fixtures/ingestion/AR'
LATEST_URL='https://www.argentina.gob.ar/sites/default/files/2026/01/ben_833_se_43_11112026-2.pdf'
OLD_URL='https://www.argentina.gob.ar/sites/default/files/2024/04/ben_635_se_1.pdf'
OLD_REF={'label':'Boletín Epidemiológico Nacional | Semana 1 Nro 635','index_url':'https://www.argentina.gob.ar/boletin-epidemiologico-nacional/boletines-2023'}


@pytest.fixture(scope='module')
def latest():
    return parse_ben_payload((FIXTURES/'ben833_cover_eno.pdf').read_bytes(),source_url=LATEST_URL)


@pytest.fixture(scope='module')
def old():
    return parse_ben_payload((FIXTURES/'ben635_cover_eno.pdf').read_bytes(),source_url=OLD_URL,reference=OLD_REF)


def test_live_eno_all_published_categories_and_zeroes(latest):
    rows,audit=latest
    assert len(rows)==43
    values={r['RawDiseaseLabel']:r['Cases'] for r in rows}
    assert values['Coqueluche (tos convulsa)']=='414'
    assert values['Hepatitis A']=='67'
    assert values['Enf. Febril Exantemática-EFE (Rubéola)']=='0'
    assert values['Tuberculosis']=='12455'
    assert 'Intento de Suicidio con resultado mortal' in values
    assert audit['report_year']==2026 and audit['report_week']==43


def test_cumulative_not_incident_and_calendar(latest):
    rows,_=latest
    assert {r['Date'] for r in rows}=={'2026-10-31'}
    assert {r['Measure'] for r in rows}=={'cumulative_case_notifications'}
    for row in rows:
        d=json.loads(row['Dimensions'])
        assert d['period_start']=='2026-01-04'
        assert d['period_end']=='2026-10-31'
        assert row['Frequency']=='weekly'
        assert row['GeographyKey']=='country:AR:national'
        assert row['DiseaseCode']=='__source_native__'


def test_historical_all_years_and_distinct_case_classification(old):
    rows,audit=old
    assert len(rows)==368
    assert {json.loads(r['Dimensions'])['source_epi_year'] for r in rows}=={2019,2020,2021,2022}
    bot=[r for r in rows if r['RawDiseaseLabel']=='Botulismo' and json.loads(r['Dimensions'])['source_epi_year']==2022]
    assert {json.loads(r['Dimensions'])['classification']:r['Cases'] for r in bot}=={'confirmed':'7','notified':'46'}
    assert len({r['SourceDiseaseCode'] for r in bot})==2
    assert audit['report_number']==635


def test_historical_blank_confirmed_is_unknown_not_zero(old):
    rows,_=old
    diar=[r for r in rows if r['RawDiseaseLabel']=='Diarreas']
    assert len(diar)==8
    assert all(r['Cases']=='' and r['MissingValueStatus']=='source_missing' for r in diar if json.loads(r['Dimensions'])['classification']=='confirmed')
    assert next(r['Cases'] for r in diar if json.loads(r['Dimensions'])['source_epi_year']==2022 and json.loads(r['Dimensions'])['classification']=='notified')=='695345'


@pytest.mark.parametrize('value',['-2','1.5','1,500','12 cases',True])
def test_malformed_counts_fail(value):
    with pytest.raises(ArgentinaBENContractError):parse_count(value)


def test_explicit_zero_preserved():
    assert parse_count('0')==('0','')
    assert parse_count('-')==('','source_missing')


def test_source_calendar_is_sunday_to_saturday():
    assert epidemiological_week_end(2024,1)==date(2024,1,6)
    assert epidemiological_week_end(2025,3)==date(2025,1,18)
    assert epidemiological_week_end(2026,43)==date(2026,10,31)
    with pytest.raises(ArgentinaBENContractError):epidemiological_week_end(2026,54)


def test_spaced_header_and_stale_cover_artwork():
    text='B o l e t í n E p i d e m i o l ó g i c o N a c i o n a l N ° 73 9 , S E 2 , A ñ o 2 0 2 5\nN°740 Año2025'
    assert source_report_identity(text)=={'report_number':739,'report_week':2,'report_year':2025}


def test_cms_broken_prefix_and_external_link_exclusion():
    payload=b'<a href="blank:#/sites/default/files/ben_820.pdf">BEN 820</a><a href="https://example.com/ben.pdf">BEN 1</a>'
    refs=links_from_html(payload,'https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional')
    assert len(refs)==1
    assert refs[0]['url']=='https://www.argentina.gob.ar/sites/default/files/ben_820.pdf'
    assert refs[0]['original_href'].startswith('blank:#')


def test_revision_chooses_later_issue_without_summing(latest):
    rows,_=latest
    first=dict(rows[0]);second={**first,'SourceReportNumber':'834','Cases':'401'}
    selected=deduplicate_revisions([first,second])
    assert len(selected)==1 and selected[0]['Cases']=='401'
    with pytest.raises(ArgentinaBENContractError):deduplicate_revisions([first,{**first,'Cases':'999'}])


def test_median_and_trend_are_not_counts():
    rows=parse_eno_table([['Grupo','Evento','Mediana Acumulado 2022-2025','Acumulado 2026 SE43','Tendencia 2022-2026'],['G','Hepatitis A','51','67','500']],report={'report_number':833,'report_year':2026,'report_week':43},url=LATEST_URL)
    assert len(rows)==1 and rows[0]['Cases']=='67'


def test_merged_grid_rows_split_only_with_matching_lines():
    rows=parse_eno_table([['Grupo','Evento','Mediana Acumulado 2022-2025','Acumulado 2026 SE25'],['G','Domestic pesticide\nLead exposure','18\n5','30\n3']],report={'report_number':815,'report_year':2026,'report_week':25},url=LATEST_URL)
    assert [(r['RawDiseaseLabel'],r['Cases']) for r in rows]==[('Domestic pesticide','30'),('Lead exposure','3')]


def test_non_pdf_fails():
    with pytest.raises(ArgentinaBENContractError):parse_ben_payload(b'<html>blocked</html>',source_url=LATEST_URL)


def test_updater_keeps_historical_and_cumulative_rows(latest):
    rows,_=latest
    updater=ArgentinaBENUpdater()
    result=updater.prepare_rows(rows,months=[(2026,11)])
    assert len(result.rows)==43
    assert updater.public_release_enabled is False
    with pytest.raises(ValueError):updater.prepare_rows(rows,start_year=2030)


def test_offline_organization_uses_captured_bytes(monkeypatch,latest):
    from src.services.ingestion.countries import ar
    payload=(FIXTURES/'ben833_cover_eno.pdf').read_bytes()
    plan={'references':[{'url':LATEST_URL}],'failures':[]}
    artifacts=[SimpleNamespace(kind='ar_ben_plan',payload=json.dumps(plan).encode()),SimpleNamespace(kind='ar_ben_pdf',source_url=LATEST_URL,metadata_={},payload=payload)]
    monkeypatch.setattr(ar,'read_artifact',lambda a:a.payload)
    result=ar.organize(artifacts,updater=ArgentinaBENUpdater())
    assert len(result.rows)==43
    assert result.source_audit['discovered_reports']==1
    assert result.rows[0]['RawSHA256']==hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_raw_persistence_failure_propagates(monkeypatch):
    from src.services.ingestion.countries import ar
    class FakeCrawler:
        session=SimpleNamespace(close=lambda:None)
        def discover(self,**kwargs):return [{'url':LATEST_URL}],[],[]
        def download(self,reference,**kwargs):return b'%PDFbytes',{}
    class Writer:
        artifacts=[]
        async def write(self,*args,**kwargs):raise OSError('disk unavailable')
    monkeypatch.setattr(ar,'ArgentinaBENCrawler',FakeCrawler)
    with pytest.raises(OSError,match='disk unavailable'):await ar.collect(Writer())


def test_four_real_eno_families_keep_counts_and_measures_separate():
    rows,audit=parse_ben_payload((FIXTURES/'ben788_four_eno_tables.pdf').read_bytes(),source_url='https://www.argentina.gob.ar/sites/default/files/2025/01/ben_788_se_51_vf2.pdf')
    assert len(rows)==111
    coq=[r for r in rows if r['RawDiseaseLabel']=='Coqueluche (tos convulsa)']
    assert {json.loads(r['Dimensions'])['classification']:r['Cases'] for r in coq}=={'notified':'6300','confirmed':'1000'}
    laboratory=[r for r in rows if r['RawDiseaseLabel']=='Infección por Chlamydia trachomatis']
    assert {r['Measure']:r['Cases'] for r in laboratory}=={'cumulative_laboratory_tests':'4599','cumulative_laboratory_positives':'275'}
    assert {r['ReportingBasis'] for r in laboratory}=={'laboratory_surveillance'}
    clinical=next(r for r in rows if r['RawDiseaseLabel']=='Diarrea')
    assert clinical['Measure']=='cumulative_clinical_notifications' and clinical['Cases']=='749811'
    assert len({r['SourceDiseaseCode'] for r in laboratory})==2


def test_table_explicit_cutoff_overrides_bulletin_week():
    rows=parse_eno_table([['SECCIÓN','EVENTO','2020-2024 Mediana acumulados SE 1-18','2025 Acumulados SE 1-18'],['G','Hepatitis A','51','67']],report={'report_number':800,'report_year':2025,'report_week':20},url=LATEST_URL)
    assert json.loads(rows[0]['Dimensions'])['source_epi_week']==18
    assert rows[0]['Date']==epidemiological_week_end(2025,18).isoformat()


def test_archive_cache_revalidation_and_damage(tmp_path,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from src.data.crawlers.ar import ArgentinaBENCrawler
    crawler=ArgentinaBENCrawler(archive_dir=tmp_path)
    ref={'url':LATEST_URL,'label':'BEN 833'}
    calls=[]
    payload=b'%PDF-original-source-bytes'
    monkeypatch.setattr(crawler,'get',lambda url:(calls.append(url) or SimpleNamespace(content=payload)))
    first,meta=crawler.download(ref)
    second,cached=crawler.download(ref)
    assert len(calls)==1 and cached['cache_reused'] is True
    path=Path(meta['archive_path']);receipt=path.with_suffix('.json')
    stale=json.loads(receipt.read_text());stale['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat();receipt.write_text(json.dumps(stale))
    crawler.download(ref)
    assert len(calls)==2
    path.write_bytes(b'%PDF-corrupt')
    crawler.download(ref)
    assert len(calls)==3
    crawler.session.close()


def test_force_refresh_does_not_mutate_captured_hardlinks(tmp_path,monkeypatch):
    import os
    from src.data.crawlers.ar import ArgentinaBENCrawler
    crawler=ArgentinaBENCrawler(archive_dir=tmp_path)
    response=SimpleNamespace(content=b'%PDF-old-official-response')
    monkeypatch.setattr(crawler,'get',lambda url:response)
    _,meta=crawler.download({'url':LATEST_URL})
    original=Path(meta['archive_path']);captured=tmp_path/'captured-original.pdf'
    os.link(original,captured)
    response.content=b'%PDF-new-source-revision'
    crawler.download({'url':LATEST_URL},force=True)
    assert captured.read_bytes()==b'%PDF-old-official-response'
    assert original.read_bytes()==b'%PDF-new-source-revision'
    crawler.session.close()
