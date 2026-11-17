from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import repair_cn_province_data as repair
from scripts.repair_cn_province_data import build_plan, write_json
from src.data.storage.series_observation_store import SeriesObservationStore


def raw(code='hepatitis_a', cases=3, **extra):
    return {'Date':'2026-09-01','SourceID':'SRC_CN_PROV_MONTHLY_REPORT',
            'SourceDiseaseCode':code,'RawDiseaseLabel':{'hepatitis_a':'甲肝','hfrs':'流行性出血热','ebola':'埃博拉出血热'}[code],
            'DefinitionVersion':'CN_PROVINCE_MONTHLY_REPORT_V1','GeographyKey':'country:CN-SX:national',
            'JurisdictionCode':'CN-SX','Cases':cases,'SourceURL':'https://official.example/report',
            'Raw':{'source_row':{'病种':'甲肝','发病数':cases}},**extra}


def old(row, series):
    return {'time':row['Date'],'series_code':series,'geography_key':row['GeographyKey'],
            'dimension_key':'all','value':row['Cases'],'raw_data':row,'updated_at':'2026-11-15 00:00:00',
            'unit':'count','quality_status':'raw','suppressed':False,'suppression_reason':None,
            'dimensions':{},'metadata':{}}


def setup(tmp_path: Path, previous, reports):
    write_json(tmp_path/'before.json.gz',{'observations':previous})
    write_json(tmp_path/'provinces/CN-SX.json',{'province':'CN-SX','reports':[
        {'date':'2026-09-01','url':'https://official.example/report','status':'parsed',
         'evidence_type':'table','rows':rows} for rows in reports]})


def test_backfill_keeps_existing_values_and_only_adds_explicit_death_metric(tmp_path):
    setup(tmp_path,[old(raw(),'SER_CN_PROV_REPORT_HEPATITIS_A')],[[raw(Deaths=0)]])
    plan=build_plan(tmp_path)
    assert plan['noops']==1
    assert len(plan['changes'])==1
    assert plan['changes'][0]['identity'][1]=='SER_CN_PROV_REPORT_DEATHS_HEPATITIS_A'
    assert plan['changes'][0]['new_value']==0
    assert not plan['errors']


def test_conflicting_documents_quarantine_entire_month_without_partial_write(tmp_path):
    setup(tmp_path,[],[[raw(cases=3)],[raw(cases=4)]])
    plan=build_plan(tmp_path)
    assert not plan['changes']
    assert plan['errors'][0]['reason']=='conflicting_official_documents'


def test_reparse_withdraws_only_unsupported_narrative_and_keeps_old_table_rows(tmp_path):
    bad=raw('hfrs',0,Raw={'explicit_narrative_value':'无埃博拉出血热病例报告'})
    setup(tmp_path,[old(bad,'SER_CN_PROV_REPORT_HFRS'),old(raw(),'SER_CN_PROV_REPORT_HEPATITIS_A')],
          [[raw('ebola',0,Raw={'explicit_narrative_value':'无埃博拉出血热病例报告'})]])
    plan=build_plan(tmp_path)
    withdrawals=[r for r in plan['changes'] if r['operation']=='withdraw_unsupported_narrative']
    assert len(withdrawals)==1
    assert withdrawals[0]['identity'][1]=='SER_CN_PROV_REPORT_HFRS'
    assert withdrawals[0]['row']['Cases'] is None
    assert withdrawals[0]['row']['MissingValueStatus']=='source_missing'
    assert withdrawals[0]['row']['Raw']['previous_value']==0
    assert not any(r['identity'][1]=='SER_CN_PROV_REPORT_HEPATITIS_A' for r in plan['changes'])


def test_invalid_registry_month_cannot_authorize_withdrawal(tmp_path):
    previous=raw('hfrs',0,Raw={'explicit_narrative_value':'无埃博拉出血热病例报告'})
    setup(tmp_path,[old(previous,'SER_CN_PROV_REPORT_HFRS')],[[raw(SourceDiseaseCode='unregistered',RawDiseaseLabel='未知病种')]])
    plan=build_plan(tmp_path)
    assert not plan['changes']
    assert plan['errors'][0]['reason']=='registry_preflight_failed'


def test_decimal_snapshot_strings_are_equal_to_parsed_numeric_values(tmp_path):
    previous=old(raw(),'SER_CN_PROV_REPORT_HEPATITIS_A')
    previous['value']=Decimal('3.0000')
    setup(tmp_path,[previous],[[raw()]])
    plan=build_plan(tmp_path)
    assert plan['noops']==1 and not plan['changes']


def test_insert_only_plan_quarantines_entire_month_with_conflicting_candidate(tmp_path):
    setup(tmp_path,[old(raw(cases=1),'SER_CN_PROV_REPORT_HEPATITIS_A')],
          [[raw(cases=3),raw('hfrs',0)]])
    plan=build_plan(tmp_path,insert_only=True)
    assert not plan['changes']
    assert plan['errors'][0]['reason']=='existing_observation_requires_review'
    assert plan['errors'][0]['old_value']==1
    assert plan['errors'][0]['candidate_value']==3


def test_insert_only_plan_adds_new_metric_when_existing_value_agrees(tmp_path):
    setup(tmp_path,[old(raw(),'SER_CN_PROV_REPORT_HEPATITIS_A')],
          [[raw(Deaths=1)]])
    plan=build_plan(tmp_path,insert_only=True)
    assert len(plan['changes'])==1
    assert plan['changes'][0]['operation']=='insert'
    assert plan['changes'][0]['identity'][1]=='SER_CN_PROV_REPORT_DEATHS_HEPATITIS_A'
    assert not plan['errors']


def test_equal_duplicate_rows_do_not_misalign_case_and_death_metric_identities(tmp_path):
    setup(tmp_path,[],[[raw(Deaths=1),raw(Deaths=1)]])
    plan=build_plan(tmp_path)
    assert len(plan['changes'])==2
    assert {c['identity'][1]:Decimal(str(c['new_value'])) for c in plan['changes']}=={
        'SER_CN_PROV_REPORT_HEPATITIS_A':Decimal(3), 'SER_CN_PROV_REPORT_DEATHS_HEPATITIS_A':Decimal(1)}


def test_positive_unsupported_narrative_needs_review_and_valid_zero_is_retained(tmp_path):
    positive=raw('hfrs',1,Raw={'explicit_narrative_value':'无埃博拉出血热病例报告'})
    setup(tmp_path,[old(positive,'SER_CN_PROV_REPORT_HFRS')],[[raw()]])
    plan=build_plan(tmp_path)
    assert not any(c['operation']=='withdraw_unsupported_narrative' for c in plan['changes'])
    assert any(e['reason']=='positive_narrative_requires_manual_review' for e in plan['errors'])
    supported=raw('hfrs',0,Raw={'explicit_narrative_value':'无流行性出血热病例报告'})
    setup(tmp_path,[old(supported,'SER_CN_PROV_REPORT_HFRS')],[[raw()]])
    plan=build_plan(tmp_path)
    assert not any(c['operation']=='withdraw_unsupported_narrative' for c in plan['changes'])


def test_one_valid_document_cannot_authorize_withdrawal_from_another_invalid_document(tmp_path):
    bad=raw('hfrs',0,SourceURL='https://official.example/bad',
            Raw={'explicit_narrative_value':'无埃博拉出血热病例报告'})
    setup(tmp_path,[old(bad,'SER_CN_PROV_REPORT_HFRS')],[])
    write_json(tmp_path/'provinces/CN-SX.json',{'province':'CN-SX','reports':[
        {'date':'2026-09-01','url':'https://official.example/good','status':'parsed',
         'evidence_type':'table','rows':[raw(SourceURL='https://official.example/good')]},
        {'date':'2026-09-01','url':'https://official.example/bad','status':'parsed',
         'evidence_type':'table','rows':[raw(SourceDiseaseCode='unknown',RawDiseaseLabel='未知病种')]},
    ]})
    plan=build_plan(tmp_path)
    assert any(e['reason']=='registry_preflight_failed' for e in plan['errors'])
    assert not any(c['operation']=='withdraw_unsupported_narrative' for c in plan['changes'])


def reviewed_update(tmp_path):
    setup(tmp_path,[old(raw(cases=1),'SER_CN_PROV_REPORT_HEPATITIS_A')],[[raw(cases=3)]])
    build_plan(tmp_path)
    plan=repair.read_json(tmp_path/'plan.json')
    before={repair.identity(r):r for r in repair.read_json(tmp_path/'before.json.gz')['observations']}
    return plan,before


def test_plan_validation_checks_reviewed_baseline_hash(tmp_path):
    plan,before=reviewed_update(tmp_path)
    store=SeriesObservationStore()
    assert repair.validate_plan_changes(plan,before,store) is None
    plan['changes'][0]['old_row_hash']='0'*64
    with pytest.raises(ValueError):
        repair.validate_plan_changes(plan,before,store)


@pytest.mark.parametrize('mutation',[
    lambda c:c.update(new_value=999),
    lambda c:c['row'].update(Cases=999),
    lambda c:c['row'].update(SourceID='SRC_CN_PROV_DATACENTER'),
    lambda c:c['row'].update(JurisdictionCode='CN-BJ'),
    lambda c:c.update(operation='insert',old_row_hash=None),
])
def test_plan_validation_rejects_value_scope_or_insert_baseline_tampering(tmp_path,mutation):
    plan,before=reviewed_update(tmp_path)
    mutation(plan['changes'][0])
    with pytest.raises(ValueError):
        repair.validate_plan_changes(plan,before,SeriesObservationStore())


def test_plan_validation_rejects_duplicate_identity(tmp_path):
    plan,before=reviewed_update(tmp_path)
    plan['changes'].append(deepcopy(plan['changes'][0]))
    with pytest.raises(ValueError):
        repair.validate_plan_changes(plan,before,SeriesObservationStore())


def test_plan_validation_rejects_unregistered_province_even_when_identity_agrees(tmp_path):
    setup(tmp_path,[],[[raw()]])
    build_plan(tmp_path)
    plan=repair.read_json(tmp_path/'plan.json')
    change=plan['changes'][0]
    change['identity'][2]='country:CN-ZZ:national'
    change['row'].update(JurisdictionCode='CN-ZZ',GeographyKey='country:CN-ZZ:national')
    with pytest.raises(ValueError):
        repair.validate_plan_changes(plan,{},SeriesObservationStore())


def fake_apply_database(monkeypatch,current):
    from src.data.processors import cn_provinces as processor

    db=SimpleNamespace(commit=AsyncMock(),execute=AsyncMock(return_value=
        SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:current))))
    @asynccontextmanager
    async def database():
        yield db
    monkeypatch.setattr('src.core.database.get_db',database)
    monkeypatch.setattr('src.core.database.dispose_database',AsyncMock())
    lock=AsyncMock();dirty=AsyncMock();imports=[];instances=[]
    monkeypatch.setattr('src.core.disease_mutation_lock.acquire_country_data_mutation_lock',lock)
    monkeypatch.setattr('src.services.ingestion.stages.mark_dirty',dirty)
    base=processor.CNProvinceUpdater
    class Updater(base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            instances.append(self)
        async def import_rows(self,db,rows):
            imports.extend(deepcopy(rows))
            return processor.CNProvinceImportResult(len(rows),len(rows),0,
                ('SRC_CN_PROV_MONTHLY_REPORT',),None,True)
    monkeypatch.setattr(processor,'CNProvinceUpdater',Updater)
    return SimpleNamespace(db=db,lock=lock,dirty=dirty,imports=imports,instances=instances)


@pytest.mark.parametrize('concurrent_change',['value','timestamp','new_row'])
async def test_apply_refuses_concurrent_changes_without_any_write(tmp_path,monkeypatch,concurrent_change):
    plan,before=reviewed_update(tmp_path)
    current=deepcopy(next(iter(before.values())))
    current['value']=Decimal(str(current['value']))
    current['updated_at']=datetime.fromisoformat(current['updated_at'])
    if concurrent_change=='value':current['value']=Decimal(9)
    elif concurrent_change=='timestamp':current['updated_at']=datetime(2026,11,16)
    else:
        setup(tmp_path,[],[[raw(cases=3)]])
        build_plan(tmp_path)
    fake=fake_apply_database(monkeypatch,[current])
    with pytest.raises(RuntimeError,match='Concurrent change'):
        await repair.apply_plan(tmp_path)
    assert not fake.imports
    fake.db.commit.assert_not_awaited()
    fake.dirty.assert_not_awaited()


async def test_apply_reuses_updater_and_does_not_expand_deaths_twice(tmp_path,monkeypatch):
    setup(tmp_path,[],[[raw(cases=3,Deaths=1)]])
    build_plan(tmp_path)
    fake=fake_apply_database(monkeypatch,[])
    receipt=await repair.apply_plan(tmp_path)
    assert len(fake.instances)==1
    assert len(fake.imports)==2
    assert {r['SourceDiseaseCode'] for r in fake.imports}=={'hepatitis_a','hepatitis_a__deaths'}
    assert all('Deaths' not in r for r in fake.imports)
    assert all(r.get('RepairPlanSHA256')==receipt['plan_sha256'] for r in fake.imports)
    assert fake.dirty.await_args.kwargs['scope_key']=='CN-SX'


@pytest.mark.parametrize('same_plan',[True,False])
async def test_restarted_apply_only_skips_rows_marked_with_this_plan(tmp_path,monkeypatch,same_plan):
    plan,before=reviewed_update(tmp_path)
    current=deepcopy(next(iter(before.values())))
    current['value']=Decimal(str(plan['changes'][0]['new_value']))
    current['updated_at']=datetime(2026,11,16)
    plan_hash=hashlib.sha256((tmp_path/'plan.json').read_bytes()).hexdigest()
    current['raw_data']={**plan['changes'][0]['row'],'RepairPlanSHA256':plan_hash if same_plan else '0'*64}
    fake=fake_apply_database(monkeypatch,[current])
    if same_plan:
        result=await repair.apply_plan(tmp_path)
        assert result['provinces'][0]['already_applied']==1
    else:
        with pytest.raises(RuntimeError,match='Concurrent change'):
            await repair.apply_plan(tmp_path)
    assert not fake.imports
    fake.dirty.assert_not_awaited()


async def test_apply_seeds_exact_prior_raw_state_in_revision_ledger_before_repair(tmp_path,monkeypatch):
    _,before=reviewed_update(tmp_path)
    current=deepcopy(next(iter(before.values())))
    current['value']=Decimal(str(current['value']))
    current['updated_at']=datetime.fromisoformat(current['updated_at'])
    fake=fake_apply_database(monkeypatch,[current])
    await repair.apply_plan(tmp_path)
    revision_calls=[call for call in fake.db.execute.await_args_list if len(call.args)==2]
    assert len(revision_calls)==1
    payload=revision_calls[0].args[1]
    assert len(payload)==1
    assert payload[0]['raw_data']==current['raw_data']
    assert payload[0]['value']==current['value']
    assert payload[0]['metadata']==current['metadata']
    assert payload[0]['recorded_at'].tzinfo is not None
    assert len(fake.imports)==1
    fake.db.commit.assert_awaited_once()
