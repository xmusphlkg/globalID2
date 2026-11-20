"""Real DGE originals verify period, metric, missing-value and replay contracts."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.mx_high_frequency import (
    MONTHLY_SCOPE, WEEKLY_SCOPE, NATIONAL_URL, WEEKLY_CURRENT_URL,
    MexicoDGEContractError, discover_monthly_tables, discover_weekly_links,
    parse_monthly_pdf, parse_weekly_pdf, _issue_period, _weekly_number,
)
from src.data.processors.mx_high_frequency import MexicoDGEHighFrequencyUpdater
from src.services.ingestion.countries import mx_high_frequency as ingestion
from src.services.mx_high_frequency_review import reviewed_mx_high_frequency_record

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/ingestion/MX/high_frequency'

@pytest.fixture(scope='module')
def monthly_refs():
    return discover_monthly_tables((FIXTURES/'dge-national-menu.html').read_bytes())

@pytest.fixture(scope='module')
def monthly(monthly_refs):
    ref = next(r for r in monthly_refs if r['year']==2025 and r['sex']=='all')
    return parse_monthly_pdf((FIXTURES/'dge-monthly-2025.pdf').read_bytes(), reference=ref)

@pytest.fixture(scope='module')
def weekly_refs():
    return discover_weekly_links((FIXTURES/'boletin-2026.html').read_bytes(), page_url=WEEKLY_CURRENT_URL, start_year=2026,end_year=2026)

@pytest.fixture(scope='module')
def weekly(weekly_refs):
    return parse_weekly_pdf((FIXTURES/'boletin-2026-week43.pdf').read_bytes(),reference=weekly_refs[-1])


def cell(rows,label,measure='case_notifications',sex='all'):
    return next(r for r in rows if r['RawDiseaseLabel'].strip('§* ')==label and r['Measure']==measure and r['Sex']==sex)


def record(row):
    return {'source_system': 'SRC_MX_DGE_MONTHLY' if row['Frequency']=='monthly' else 'SRC_MX_DGE_WEEKLY',
        'geography_key':row['GeographyKey'],'metric_type':row['Measure'],'series_unit':row['Unit'],
        'observation_unit':row['Unit'],'dimensions':json.loads(row['Dimensions']),
        'reviewed_source_label':row['RawDiseaseLabel'],'reviewed_source_code':row['SourceDiseaseCode'],
        'date':date.fromisoformat(row['Date']),'definition_version':row['DefinitionVersion'],
        'reporting_basis':row['ReportingBasis'],'temporal_granularity':row['Frequency']}


def test_real_discovery_preserves_legacy_paths_and_actual_pdf_links(monthly_refs,weekly_refs):
    assert len(monthly_refs)==69
    assert next(r for r in monthly_refs if r['year']==2003 and r['sex']=='female')['edition_path']=='5000'
    assert len(weekly_refs)==43
    assert weekly_refs[-1]['issue_week']==43
    assert weekly_refs[-1]['url'].endswith('/1115232/sem43.pdf')


def test_months_reconcile_to_original_annual_totals_and_keep_explicit_zero(monthly):
    rows,audit=monthly
    assert len(rows)==2256 and not audit['failures']
    abscess=[r for r in rows if r['RawDiseaseLabel']=='Absceso hepático amebiano']
    assert [r['Cases'] for r in abscess[:2]]==['41','43']
    assert sum(int(r['Cases']) for r in abscess)==598
    influenza=[r for r in rows if r['RawDiseaseLabel']=='Influenza']
    assert [int(r['Cases']) for r in influenza]==[3182,2672,2203,1662,820,364,338,220,318,391,484,1984]
    assert len([r for r in rows if r['RawDiseaseLabel']=='Cólera' and r['Cases']=='0'])==12
    assert any('Diabetes' in r['RawDiseaseLabel'] for r in rows)
    assert _weekly_number('-',zero_dash=False)==('','source_missing')


def test_each_weekly_table_retains_its_own_verified_period(weekly):
    rows,audit=weekly
    assert audit['issue_week']==43
    cholera=cell(rows,'Cólera')
    assert cholera['Cases']=='0' and cholera['Date']=='2026-10-25'
    varicella=cell(rows,'Varicela')
    assert varicella['Cases']=='1014' and varicella['Date']=='2026-10-18'
    assert varicella['PeriodEnd']=='2026-10-24'
    assert json.loads(varicella['Dimensions'])['source_epi_week']==42
    assert cell(rows,'Infecciones respiratorias agudas')['Cases']=='342915'
    assert not audit['failures']
    assert len(audit['excluded_source_cells'])==8
    assert audit['status']=='partial'


def test_cumulative_and_sex_strata_never_become_incident_cases(weekly):
    rows,_=weekly
    current=cell(rows,'Meningitis tuberculosa')
    cumulative=cell(rows,'Meningitis tuberculosa','cumulative_case_notifications')
    assert current['Cases']=='8' and cumulative['Cases']=='465'
    assert current['SourceDiseaseCode']!=cumulative['SourceDiseaseCode']
    assert cumulative['PeriodStart']=='' and cumulative['PeriodType']=='cumulative'
    assert cell(rows,'Enteritis debida a rotavirus','cumulative_case_notifications','male')['Cases']=='261'
    assert cell(rows,'Enteritis debida a rotavirus','cumulative_case_notifications','female')['Cases']=='242'
    assert reviewed_mx_high_frequency_record(record(current),'MX')
    assert reviewed_mx_high_frequency_record(record(cumulative),'MX') is None
    assert all('source_page' not in json.loads(r['Dimensions']) for r in rows)


def test_publication_guard_uses_actual_export_units_and_rejects_estimates(monthly,weekly):
    monthly_valid=record(cell(monthly[0],'Influenza'))
    assert reviewed_mx_high_frequency_record(monthly_valid,'MX')
    assert reviewed_mx_high_frequency_record({**monthly_valid,'observation_unit':'per_100000'},'MX') is None
    assert reviewed_mx_high_frequency_record({**monthly_valid,'series_unit':None},'MX') is None
    estimated=next(r for r in monthly[0] if r['ReportingBasis']=='monthly_estimated_cases')
    assert reviewed_mx_high_frequency_record(record(estimated),'MX') is None
    current=record(cell(weekly[0],'Varicela'))
    assert reviewed_mx_high_frequency_record(current,'MX')
    assert reviewed_mx_high_frequency_record(record(cell(weekly[0],'Fiebre amarilla')),'MX') is None
    current['dimensions']={**current['dimensions'],'sex':'female'}
    assert reviewed_mx_high_frequency_record(current,'MX') is None


def test_cross_month_cover_dates_come_from_source():
    assert _issue_period('Semana 40 / del 27 de septiembre al 3 de octubre del 2026',{'year':2026,'issue_week':40})[2:]==(date(2026,9,27),date(2026,10,3))
    with pytest.raises(MexicoDGEContractError):
        _issue_period('Semana 40 / del 28 de septiembre al 3 de octubre del 2026',{'year':2026,'issue_week':40})


def test_scopes_have_independent_identity_and_registry_contract():
    month=MexicoDGEHighFrequencyUpdater(source=MONTHLY_SCOPE)
    week=MexicoDGEHighFrequencyUpdater(source=WEEKLY_SCOPE)
    assert month.ontology_source_id=='SRC_MX_DGE_MONTHLY'
    assert week.ontology_source_id=='SRC_MX_DGE_WEEKLY'
    assert month.source_scope!=week.source_scope
    assert month.series_registry_coverage=='required' and month.public_release_enabled


def test_offline_organization_verifies_bytes_and_frozen_discovery(monthly_refs,monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    directory=Path('data/raw/mx');directory.mkdir(parents=True)
    def artifact(payload,kind,url):
        digest=hashlib.sha256(payload).hexdigest();path=directory/digest;path.write_bytes(payload)
        return SimpleNamespace(kind=kind,source_url=url,sha256=digest,byte_size=len(payload),relative_path=str(path),metadata_={})
    refs=[r for r in monthly_refs if r['year']==2025]
    ref=next(r for r in refs if r['sex']=='all')
    pdf=artifact((FIXTURES/'dge-monthly-2025.pdf').read_bytes(),'mx_hf_pdf',ref['url'])
    index=artifact((FIXTURES/'dge-national-menu.html').read_bytes(),'mx_hf_index',NATIONAL_URL)
    plan={'scope':MONTHLY_SCOPE,'references':refs,'captured':[{'url':pdf.source_url,'sha256':pdf.sha256}],
        'failures':[{'url':r['url'],'stage':'download','error':'not captured in fixture'} for r in refs if r!=ref],
        'requested_start_year':2025,'requested_end_year':2025,'latest_reports':None,'index_urls':[NATIONAL_URL]}
    frozen=artifact(json.dumps(plan).encode(),'mx_hf_plan',NATIONAL_URL)
    monkeypatch.setattr(ingestion.MexicoDGEHighFrequencyCrawler,'get',lambda *a,**k:pytest.fail('live network during replay'))
    result=ingestion.organize([pdf,index,frozen],updater=MexicoDGEHighFrequencyUpdater())
    assert len(result.rows)==2256 and not result.source_audit['coverage_complete']
    with pytest.raises(MexicoDGEContractError,match='manifest'):
        ingestion.organize([index,frozen],updater=MexicoDGEHighFrequencyUpdater())
    Path(pdf.relative_path).write_bytes(b'corrupted original')
    with pytest.raises(ValueError):
        ingestion.organize([pdf,index,frozen],updater=MexicoDGEHighFrequencyUpdater())


@pytest.fixture(scope='module')
def weekly44():
    return parse_weekly_pdf((FIXTURES/'boletin-2026-week44.pdf').read_bytes(),
        reference={'year':2026,'issue_week':44,'url':'https://www.gob.mx/cms/uploads/attachment/file/1116659/Boletin-4426.pdf'},
        title_ocr=json.loads((FIXTURES/'boletin-2026-week44-title-ocr.json').read_text()))


def test_graphic_titles_use_frozen_ocr_but_numerical_values_use_source_text(weekly44):
    rows,audit=weekly44
    assert len(rows)==154
    current=cell(rows,'Meningitis tuberculosa')
    assert current['Cases']=='5' and current['Date']=='2026-11-01'
    assert cell(rows,'Meningitis tuberculosa','cumulative_case_notifications')['Cases']=='470'
    assert cell(rows,'Influenza')['Cases']=='374'
    varicella=cell(rows,'Varicela')
    assert varicella['Cases']=='906' and varicella['Date']=='2026-10-25'
    assert json.loads(varicella['Dimensions'])['source_epi_week']==43
    unknown=cell(rows,'Cólera')
    assert unknown['Cases']=='' and unknown['MissingValueStatus']=='source_missing'
    assert json.loads(unknown['Dimensions'])['dash_semantics']=='source_missing'
    assert json.loads(current['SourceProvenance'])['source_caption_ocr']['source_epi_week']==44
    assert audit['status']=='partial' and audit['failed_count']==75


def test_title_evidence_rejects_wrong_original_disagreement_and_changed_cutoff():
    from copy import deepcopy
    from src.data.crawlers.mx_title_ocr import verify_caption_evidence
    evidence=json.loads((FIXTURES/'boletin-2026-week44-title-ocr.json').read_text())
    kwargs={'pdf_sha256':evidence['pdf_sha256'],'issue_year':2026,'issue_week':44}
    assert set(verify_caption_evidence(evidence,**kwargs))=={4,5}
    with pytest.raises(ValueError,match='different PDF'):
        verify_caption_evidence(evidence,**{**kwargs,'pdf_sha256':'0'*64})
    changed=deepcopy(evidence);changed['captions'][0]['independent_ocr_texts'][1]+=' altered'
    with pytest.raises(ValueError,match='agreement'):
        verify_caption_evidence(changed,**kwargs)
    changed=deepcopy(evidence);changed['captions'][0]['source_epi_week']=43
    with pytest.raises(ValueError,match='cutoff'):
        verify_caption_evidence(changed,**kwargs)


@pytest.fixture(scope='module')
def weekly44_all_tables():
    return parse_weekly_pdf((FIXTURES/'boletin-2026-week44.pdf').read_bytes(),
        reference={'year':2026,'issue_week':44,'url':'https://www.gob.mx/cms/uploads/attachment/file/1116659/Boletin-4426.pdf'},
        title_ocr=json.loads((FIXTURES/'boletin-2026-week44-all-titles-ocr.json').read_text()))


def test_full_graphic_caption_replay_keeps_actual_cutoffs_and_unknown_cells(weekly44_all_tables):
    rows,audit=weekly44_all_tables
    assert len(rows)==635 and len(audit['pages'])==77 and not audit['failures']
    assert len(audit['excluded_source_cells'])==8
    assert cell(rows,'Herpes genital')['Cases']=='128'
    assert cell(rows,'Vulvovaginitis')['Cases']=='11570'
    assert cell(rows,'Vulvovaginitis','cumulative_case_notifications')['Cases']=='529945'
    assert cell(rows,'Herpes genital')['Date']=='2026-10-25'
    assert cell(rows,'Meningitis tuberculosa')['Date']=='2026-11-01'
    # The original has duplicate glyph baselines near TOTAL; read its aligned
    # value once, without concatenating the adjacent printed text as "4 4".
    assert cell(rows,'Enfermedad invasiva por neumococo')['Cases']=='4'
    assert cell(rows,'Enterobiasis')['Cases']=='161'
    assert cell(rows,'Tifo murino','cumulative_case_notifications','male')['Cases']=='15'
    assert cell(rows,'Cólera')['MissingValueStatus']=='source_missing'
    identities=[(r['Date'],r['SourceDiseaseCode'],r['DefinitionVersion'],r['GeographyKey']) for r in rows]
    assert len(identities)==len(set(identities))
    meningitis=cell(rows,'Meningitis tuberculosa')
    assert json.loads(meningitis['SourceProvenance'])['equivalent_source_pages']==[4,6]


def test_special_layouts_and_explicit_caption_aliases_repair_real_source_weeks(weekly):
    rows,audit=weekly
    assert len(rows)==637 and not audit['failures']
    assert cell(rows,'Vulvovaginitis')['Cases']=='12357'
    assert cell(rows,'Hiperplasia de próstata')['Cases']=='2435'
    assert cell(rows,'Tripanosomiasis americana (enfermedad de Chagas) aguda')['Cases']=='0'
    assert cell(rows,'Tripanosomiasis americana (enfermedad de Chagas) crónica')['Cases']=='5'
    assert cell(rows,'Tumor maligno de la mama','cumulative_case_notifications','male')['Cases']=='302'
    assert cell(rows,'Tumor maligno de la mama','cumulative_case_notifications','female')['Cases']=='22532'
    assert cell(rows,'Displasia cervical leve y moderada')['Cases']=='695'
    assert cell(rows,'Tumor maligno del ovario')['Cases']=='76'
    for label in ('&Influenza','&Covid 19','Meningoencefalitis amebiana primaria'):
        matches=[r for r in rows if r['RawDiseaseLabel'].strip('§* ')==label]
        assert len(matches)>=3 and {r['Date'] for r in matches}=={'2026-10-25'}
    esavi=next(r for r in rows if '(ESAVI)' in r['RawDiseaseLabel'] and r['Measure']=='case_notifications')
    assert esavi['Cases']=='275' and esavi['Date']=='2026-10-25'


def test_clinical_stage_table_only_emits_explicit_weekly_total(weekly,weekly44_all_tables):
    for result,expected,page in ((weekly,'426',32),(weekly44_all_tables,'358',27)):
        rows,audit=result
        hiv=cell(rows,'Infección por el virus de la inmunodeficiencia humana')
        assert hiv['Cases']==expected
        provenance=json.loads(hiv['SourceProvenance'])
        assert page in provenance['equivalent_source_pages']
        assert len(provenance['excluded_source_cells'])==8
        excluded=audit['excluded_source_cells']
        assert len(excluded)==8 and {r['clinical_stage'] for r in excluded}=={'1','2','3','4'}
        assert {r['reason'] for r in excluded}=={'clinical_stage_temporal_measure_not_explicit'}


def test_full_caption_evidence_rejects_invalid_exception_and_duplicate_page():
    from copy import deepcopy
    from src.data.crawlers.mx_title_ocr import verify_caption_evidence
    evidence=json.loads((FIXTURES/'boletin-2026-week44-all-titles-ocr.json').read_text())
    kwargs={'pdf_sha256':evidence['pdf_sha256'],'issue_year':2026,'issue_week':44}
    assert set(verify_caption_evidence(evidence,**kwargs))==set(range(4,81))
    changed=deepcopy(evidence)
    item=changed['captions'][2]
    item['caption']=item['caption'].replace('hasta la 44','hasta la 45')
    item['independent_ocr_texts']=[item['caption']]*2
    with pytest.raises(ValueError,match='exception cutoff'):
        verify_caption_evidence(changed,**kwargs)
    changed=deepcopy(evidence);changed['captions'].append(changed['captions'][0])
    with pytest.raises(ValueError,match='duplicated'):
        verify_caption_evidence(changed,**kwargs)


def test_missing_weekly_heading_blocks_clinical_table():
    import pdfplumber
    from src.data.crawlers.mx_weekly_tables import parse_national_total
    with pdfplumber.open(FIXTURES/'boletin-2026-week43.pdf') as document:
        words=document.pages[31].dedupe_chars(tolerance=.7,extra_attrs=()).extract_words(x_tolerance=1,y_tolerance=2)
    words=[w for w in words if w['text']!='Sem.']
    with pytest.raises(MexicoDGEContractError,match='headers are not verified'):
        parse_national_total(words,caption_evidence=None,issue_year=2026,issue_week=43,
            issue_start=date(2026,10,25),issue_end=date(2026,10,31),page_number=32,
            source_url='https://www.gob.mx/cms/uploads/attachment/file/1115232/sem43.pdf',
            digest='a'*64,retrieved_at=None,legend_pages=[86])


def test_conflicting_reprinted_national_totals_are_quarantined(weekly44_all_tables):
    from src.data.crawlers.mx_high_frequency import deduplicate_rows
    row=cell(weekly44_all_tables[0],'Meningitis tuberculosa')
    kept,failures=deduplicate_rows([row,{**row,'Cases':'6','SourcePage':'6'}])
    assert not kept and len(failures)==1
    assert failures[0]['identity']==[row['Date'],row['SourceDiseaseCode'],row['DefinitionVersion'],row['GeographyKey']]


def test_frozen_weekly_plan_rejects_missing_and_tampered_ocr_artifact(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    directory=Path('data/raw/mx');directory.mkdir(parents=True)
    url='https://www.gob.mx/cms/uploads/attachment/file/1116659/Boletin-4426.pdf'
    html=f'<h1>Boletín Epidemiológico 2026</h1><a href="{url}">Semana 44</a>'.encode()
    refs=discover_weekly_links(html,page_url=WEEKLY_CURRENT_URL,start_year=2026,end_year=2026)
    def artifact(payload,kind,url):
        digest=hashlib.sha256(payload).hexdigest();path=directory/digest;path.write_bytes(payload)
        return SimpleNamespace(kind=kind,source_url=url,sha256=digest,byte_size=len(payload),relative_path=str(path),metadata_={})
    index=artifact(html,'mx_hf_index',WEEKLY_CURRENT_URL)
    pdf=artifact((FIXTURES/'boletin-2026-week44.pdf').read_bytes(),'mx_hf_pdf',url)
    ocr=artifact((FIXTURES/'boletin-2026-week44-all-titles-ocr.json').read_bytes(),'mx_hf_title_ocr',url)
    plan={'scope':WEEKLY_SCOPE,'references':refs,'captured':[{'url':url,'sha256':pdf.sha256}],
          'title_ocr_captured':[{'url':url,'sha256':ocr.sha256}],'failures':[],
          'requested_start_year':2026,'requested_end_year':2026,'latest_reports':None,
          'index_urls':[WEEKLY_CURRENT_URL]}
    frozen=artifact(json.dumps(plan).encode(),'mx_hf_plan',WEEKLY_CURRENT_URL)
    updater=MexicoDGEHighFrequencyUpdater(source=WEEKLY_SCOPE)
    with pytest.raises(MexicoDGEContractError,match='OCR artifact manifest'):
        ingestion.organize([index,pdf,frozen],updater=updater,source=WEEKLY_SCOPE)
    Path(ocr.relative_path).write_bytes(b'changed OCR evidence')
    with pytest.raises(ValueError):
        ingestion.organize([index,pdf,ocr,frozen],updater=updater,source=WEEKLY_SCOPE)


def test_direct_refresh_uses_verified_graphic_titles_without_claiming_complete_coverage(monkeypatch):
    from src.data.crawlers.mx_high_frequency import MexicoDGEHighFrequencyCrawler
    from src.data.crawlers import mx_title_ocr
    evidence=json.loads((FIXTURES/'boletin-2026-week44-all-titles-ocr.json').read_text())
    ref={'year':2026,'issue_week':44,'url':'https://www.gob.mx/cms/uploads/attachment/file/1116659/Boletin-4426.pdf'}
    crawler=MexicoDGEHighFrequencyCrawler(source=WEEKLY_SCOPE)
    monkeypatch.setattr(crawler,'discover',lambda **kwargs:([ref],[]))
    monkeypatch.setattr(crawler,'download',lambda *args,**kwargs:((FIXTURES/'boletin-2026-week44.pdf').read_bytes(),{}))
    monkeypatch.setattr(mx_title_ocr,'capture_summary_captions',lambda body:evidence)
    try:
        rows=crawler.fetch_rows(start_year=2026,end_year=2026)
        assert len(rows)==635 and not crawler.last_audit['failures']
        assert not crawler.last_audit['coverage_complete']
        assert len(crawler.last_audit['reports'][0]['excluded_source_cells'])==8
    finally:
        crawler.close()


def test_all_real_facts_resolve_one_registered_series_including_label_footnotes(monthly,weekly,weekly44):
    from src.data.storage.series_observation_store import SeriesObservationStore
    from src.ontology.disease import DiseaseOntology
    root=Path(__file__).resolve().parents[2]
    from src.ontology import read_ontology_document
    document=read_ontology_document(root/'configs/disease_ontology.json')
    patch=json.loads((root/'configs/high_frequency_mexico_ontology.json').read_text())
    for key in ('sources','source_series','availability'):
        definitions={r['id']:r for r in document[key]};definitions.update({r['id']:r for r in patch[key]})
        document[key]=list(definitions.values())
    store=SeriesObservationStore(DiseaseOntology.from_dict(document))
    for rows,sid in [(monthly[0],'SRC_MX_DGE_MONTHLY'),(weekly[0]+weekly44[0],'SRC_MX_DGE_WEEKLY')]:
        result=store.build_observations(rows,'MX',source_id=sid)
        assert len(result.observations)==len(rows)
        assert not result.unhandled_rows
    mappings=json.loads((root/'configs/high_frequency_mexico_reviewed_mappings.json').read_text())['rows']
    varicella=[r for r in mappings if r['local_name']=='Varicela' and r['local_code'].endswith('__all__case_notifications')]
    assert varicella and all(r['target_code']=='D054' and r['mapping_relation']=='exact' for r in varicella)
    contradictory=[r for r in mappings if r['source_id']=='SRC_MX_DGE_WEEKLY' and r['local_name']=='Fiebre amarilla']
    assert contradictory and all(r['projection_policy']=='no_projection' for r in contradictory)


def test_real_source_receipts_cover_exact_bytes():
    for item in json.loads((FIXTURES/'provenance.json').read_text()):
        body=(FIXTURES/item['fixture']).read_bytes()
        assert hashlib.sha256(body).hexdigest()==item['sha256']
        assert len(body)==item['byte_size']
