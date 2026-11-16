#!/usr/bin/env python3
"""Read-only, cell-by-cell ZA NICD database acceptance against frozen originals."""
from __future__ import annotations
import asyncio
from collections import Counter
import csv
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy import text
from src.core.database import get_db,dispose_database


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def number(value):
    return None if value in ('',None) else format(Decimal(str(value)).normalize(),'f')


def identity(code,version,day,geo,dimensions):
    return digest([code,version,day,geo,dimensions])


async def main():
    receipt_path=ROOT/'data/current/za/database_import_receipt.json'
    if not receipt_path.exists():raise SystemExit('Completed formal ZA import receipt is required.')
    receipt=json.loads(receipt_path.read_text())
    csv_path=ROOT/'data/current/za/south_africa_nicd_nmc.csv'
    manifest_path=ROOT/'data/raw/za/nicd_nmc_full_history/artifacts_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    expected_artifacts=sorted((r['source_url'],r['sha256'],r['byte_size'],r['kind']) for r in manifest)
    evidence_pairs={(r['source_url'],r['sha256']) for r in manifest if r['kind']=='za_nicd_report'}
    rows=list(csv.DictReader(csv_path.open()))
    expected={};expected_status=Counter();expected_geo=Counter();expected_period=Counter()
    expected_series=set();expected_zeroes=0
    raw_fields=list(rows[0])
    for row in rows:
        dimensions=json.loads(row['Dimensions'])
        key=identity(row['SourceDiseaseCode'],row['DefinitionVersion'],row['Date'],row['GeographyKey'],dimensions)
        assert key not in expected,'Duplicate frozen CSV source-cell identity'
        assert (row['SourceURL'],row['RawSHA256']) in evidence_pairs,'CSV has no captured original'
        expected[key]=(digest(row),digest([number(row['Cases']),row['Unit'],row['Measure'],row['ReportingBasis'],row['Frequency']]))
        expected_status[row['MissingValueStatus'] or 'numeric']+=1
        expected_geo[row['GeographyKey']]+=1
        expected_period[row['Frequency']+':'+row['PeriodType']+':'+row['Measure']]+=1
        expected_series.add((row['SourceDiseaseCode'],row['DefinitionVersion']))
        expected_zeroes+=row['Cases']=='0'
    report={'mode':'independent_readonly_database_acceptance','country':'ZA','source_id':'SRC_ZA_NICD_NMC',
        'checked_at':datetime.now(timezone.utc).isoformat(),'task_uuid':receipt['task_uuid'],
        'csv_sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'expected_rows':len(rows),'expected_statuses':dict(expected_status),
        'expected_source_series':len(expected_series),'expected_zeroes':expected_zeroes,
        'raw_capture_complete':True,'numerical_coverage_complete':False,'public_release_enabled':False,
        'legacy_projection_checked':False}
    async with get_db() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        task=(await db.execute(text('SELECT task_uuid,status::text AS status,progress,last_error,started_at,completed_at FROM tasks WHERE task_uuid=:task'),{'task':receipt['task_uuid']})).mappings().one()
        report['task']=dict(task)
        assert task['status'].casefold()=='completed' and task['progress']==100 and not task['last_error'],task
        stages=(await db.execute(text('SELECT stage,status,attempt FROM run_stages WHERE task_uuid=:task ORDER BY attempt,stage'),{'task':receipt['task_uuid']})).mappings().all()
        report['stages']=[dict(r) for r in stages]
        assert {'collect','organize','store'} <= {r['stage'] for r in stages if r['status']=='completed'}
        artifacts=(await db.execute(text('SELECT relative_path,source_url,sha256,byte_size,kind FROM source_artifacts WHERE task_uuid=:task'),{'task':receipt['task_uuid']})).mappings().all()
        assert sorted((r['source_url'],r['sha256'],r['byte_size'],r['kind']) for r in artifacts)==expected_artifacts,'Registered artifacts differ from frozen manifest'
        for item in artifacts:
            raw_path=(ROOT/item['relative_path']).resolve()
            assert raw_path.is_relative_to((ROOT/'data/raw').resolve())
            payload=raw_path.read_bytes()
            assert len(payload)==item['byte_size'] and hashlib.sha256(payload).hexdigest()==item['sha256'],'Registered original bytes differ'
        report['registered_artifacts']=len(artifacts)
        query=text('''SELECT s.source_series_code,s.definition_version,s.metric_type,s.reporting_basis,s.temporal_granularity,
            o.time,o.geography_key,o.dimensions,o.value,o.unit,o.raw_data,o.metadata,o.suppressed,o.quality_status
            FROM disease_series_observations o JOIN disease_surveillance_series s USING(series_code)
            WHERE s.country_code='ZA' AND s.source_system='SRC_ZA_NICD_NMC' ''')
        stream=await db.stream(query.execution_options(yield_per=1000))
        counts=Counter();geography=Counter();periods=Counter();errors=Counter();samples=[];checked=0;zeroes=0
        async for row in stream.mappings():
            checked+=1;raw=row['raw_data'];metadata=row['metadata'];day=row['time'].date().isoformat()
            key=identity(row['source_series_code'],row['definition_version'],day,row['geography_key'],row['dimensions'])
            wanted=expected.pop(key,None)
            actual_raw=digest({field:raw.get(field) for field in raw_fields})
            actual_contract=digest([number(row['value']),row['unit'],row['metric_type'],row['reporting_basis'],row['temporal_granularity']])
            if wanted is None:errors['unexpected_database_identity']+=1
            else:
                if actual_raw!=wanted[0]:errors['raw_source_cell_or_provenance_mismatch']+=1
                if actual_contract!=wanted[1]:errors['value_or_series_contract_mismatch']+=1
            status=raw.get('MissingValueStatus') or None;counts[status or 'numeric']+=1
            geography[row['geography_key']]+=1
            periods[row['temporal_granularity']+':'+str(raw.get('PeriodType'))+':'+row['metric_type']]+=1
            zeroes+=row['value']==0
            if metadata.get('missing_value_status')!=status:errors['missing_status_metadata_mismatch']+=1
            if status=='source_missing' and (row['value'] is not None or row['suppressed']):errors['unknown_became_value_or_suppression']+=1
            if status is None and row['value'] is None:errors['numeric_became_null']+=1
            if metadata.get('source_id')!='SRC_ZA_NICD_NMC' or raw.get('Source')!='South Africa NICD NMC':errors['source_identity_mismatch']+=1
            if raw.get('PublicReleaseEnabled')!='false' or metadata.get('dynamic_holding') is not True:errors['private_holding_flag_mismatch']+=1
            if (raw.get('SourceURL'),raw.get('RawSHA256')) not in evidence_pairs:errors['missing_original_evidence']+=1
            if row['quality_status']!='provisional':errors['quality_status_mismatch']+=1
            if row['temporal_granularity']=='monthly' and (day[-2:]!='01' or raw.get('SourcePeriodStart') or raw.get('SourcePeriodEnd')):errors['invented_month_boundary']+=1
            if row['temporal_granularity']=='quarterly' and (day!='2026-01-01' or raw.get('SourcePeriodStart')!='2026-01-01' or raw.get('SourcePeriodEnd')!='2026-03-31'):errors['quarter_changed_or_split']+=1
            if row['value'] is not None and (row['value']<0 or row['value']!=int(row['value'])):errors['invalid_count']+=1
            if len(samples)<8 and (raw.get('MissingValueStatus') or 'agricultural_or_stock_remedy_poisoning:patient_status_deceased' in row['source_series_code']):
                samples.append({'code':row['source_series_code'],'date':day,'geography':row['geography_key'],'value':row['value'],'missing_status':status,'source_url':raw['SourceURL'],'raw_sha256':raw['RawSHA256']})
        series=(await db.execute(text('''SELECT count(*) AS series,
            count(*) FILTER (WHERE disease_id IS NOT NULL OR target_group_code IS DISTINCT FROM 'G_UNMAPPED_SOURCE_CATEGORIES'
              OR mapping_relation IS DISTINCT FROM 'unmapped' OR comparability IS DISTINCT FROM 'not_comparable'
              OR aggregation_policy IS DISTINCT FROM 'non_additive' OR missing_value_policy IS DISTINCT FROM 'missing_is_unknown'
              OR temporal_granularity NOT IN ('monthly','quarterly') OR (metadata->>'dynamic_holding') IS DISTINCT FROM 'true') AS invalid_series
            FROM disease_surveillance_series WHERE country_code='ZA' AND source_system='SRC_ZA_NICD_NMC' '''))).mappings().one()
        categories=(await db.execute(text("SELECT count(*) FROM source_disease_categories WHERE country_code='ZA' AND source_id='SRC_ZA_NICD_NMC'"))).scalar_one()
        errors['missing_csv_identity']=len(expected)
        errors['status_count_mismatch']=int(counts!=expected_status)
        errors['geography_count_mismatch']=int(geography!=expected_geo)
        errors['period_count_mismatch']=int(periods!=expected_period)
        errors['zero_count_mismatch']=int(zeroes!=expected_zeroes)
        errors['series_contract_mismatch']=series['invalid_series']
        errors['source_series_count_mismatch']=int(series['series']!=len(expected_series))
        errors['source_category_count_mismatch']=int(categories!=len(expected_series))
        report.update(database_rows=checked,database_statuses=dict(counts),database_zeroes=zeroes,
            geography_counts=dict(geography),period_counts=dict(periods),series=dict(series),source_categories=categories,
            invariants=dict(errors),source_samples=samples,
            status='passed' if not any(errors.values()) and checked==len(rows) else 'failed')
    path=ROOT/'data/current/za/database_acceptance.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    if report['status']!='passed':raise SystemExit(1)


async def entry():
    try:await main()
    finally:await dispose_database()


if __name__=='__main__':asyncio.run(entry())
