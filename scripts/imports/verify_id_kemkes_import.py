"""Read-only, full row reconciliation of Indonesia's imported profile archive."""
from __future__ import annotations
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from sqlalchemy import text
from src.core.country_library import get_country_bootstrap_config
from src.core.database import get_db,dispose_database
from src.services.ingestion.artifacts import read_artifact


def identity(row):
    return (row['Date'],row['SourceDiseaseCode'],row['DefinitionVersion'],row['GeographyKey'],
        json.dumps(json.loads(row['Dimensions']),ensure_ascii=False,sort_keys=True))


async def main():
    csv_path=ROOT/'data/current/id/indonesia_kemkes_profile.csv'
    receipt=json.loads((ROOT/'data/current/id/database_import_receipt.json').read_text())
    manifest_path=Path(receipt['source_manifest']);manifest=json.loads(manifest_path.read_text())
    expected=list(csv.DictReader(csv_path.open()));source_audit=json.loads((ROOT/'data/current/id/source_audit.json').read_text())
    expected_by_key={identity(r):r for r in expected}
    assert len(expected_by_key)==len(expected)==receipt['records']==source_audit['rows']
    for record in manifest['artifacts']:read_artifact(SimpleNamespace(**record))
    plans=[a for a in manifest['artifacts'] if a['metadata_'].get('endpoint')=='collection_audit']
    assert len(plans)==1
    plan=json.loads(read_artifact(SimpleNamespace(**plans[0])))
    source_pages={a['year']:a['pages'] for a in source_audit['reports']}
    for row in expected:
        reference=next((item['reference'] for item in plan['captured']
            if item['sha256']==row['RawSHA256'] and item['reference']['year']==int(row['SourceReportYear'])),None)
        assert reference is not None and row['SourceURL']==reference['url']
        assert row['SourceLandingPageURL']==reference['page_url']
        assert 1<=int(row['SourcePage'])<=source_pages[int(row['SourceReportYear'])]
        dimensions=json.loads(row['Dimensions'])
        assert dimensions['source_report_sha256']==row['RawSHA256']
        assert dimensions['source_page']==int(row['SourcePage'])
        assert dimensions['source_statistic_year']==int(row['Year'])
        assert dimensions['source_profile_year']==int(row['SourceReportYear'])
    async with get_db() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        task=(await db.execute(text('SELECT status,progress,output_data FROM tasks WHERE task_uuid=:task'),{'task':receipt['task_uuid']})).mappings().one()
        assert task['status'].lower()=='completed' and task['progress']==100
        database=(await db.execute(text('''SELECT o.time,o.value,o.geography_key,o.dimensions,o.raw_data,o.metadata,
            o.unit,o.quality_status,o.suppressed,s.source_series_code,s.definition_version,s.metric_type,
            s.reporting_basis,s.temporal_granularity,s.disease_id,s.mapping_relation,s.comparability,
            s.aggregation_policy,s.metadata AS series_metadata,c.source_code AS category_code,
            c.definition_version AS category_definition_version,c.source_id AS category_source_id
            FROM disease_series_observations o
            JOIN disease_surveillance_series s ON s.series_code=o.series_code
            JOIN source_disease_categories c ON c.id=(s.metadata->>'source_category_id')::bigint
            WHERE s.country_code=:country AND s.source_system=:source'''),
            {'country':'ID','source':'SRC_ID_KEMKES_PROFILE'})).mappings().all()
        assert len(database)==len(expected)
        actual_keys=[]; mismatch=[]
        for observed in database:
            raw=observed['raw_data'];key=identity(raw);actual_keys.append(key)
            row=expected_by_key.get(key)
            if row is None:mismatch.append({'reason':'unexpected native identity','key':key[:4]});continue
            failures=[]
            def check(condition,name):
                if not condition:failures.append(name)
            check(raw==row,'full raw row')
            check(observed['time'].date().isoformat()==row['Date'],'statistic date')
            check(observed['value']==(float(row['Cases']) if row['Cases'] else None),'value/null')
            check(observed['dimensions']==json.loads(row['Dimensions']),'source report year/statistic year/page/hash/evidence dimensions')
            check(observed['geography_key']==row['GeographyKey'],'national geography')
            check(observed['metric_type']==row['Measure']==observed['metadata']['measure'],'measure')
            check(observed['unit']==row['Unit'],'unit')
            check(observed['temporal_granularity']==row['Frequency'],'frequency')
            check(observed['reporting_basis']==row['ReportingBasis'],'reporting basis')
            check(observed['source_series_code']==row['SourceDiseaseCode']==observed['category_code'],'source category')
            check(observed['definition_version']==row['DefinitionVersion']==observed['category_definition_version'],'definition version')
            check(observed['category_source_id']=='SRC_ID_KEMKES_PROFILE','source namespace')
            check(observed['metadata'].get('missing_value_status')==('source_missing' if row['Cases']=='' else None),'explicit missing')
            check(row['PublicReleaseEnabled']=='false','private source row')
            check(observed['series_metadata'].get('dynamic_holding') is True,'holding series')
            check(observed['disease_id'] is None and observed['mapping_relation']=='unmapped','no canonical projection')
            check(observed['comparability']=='not_comparable' and observed['aggregation_policy']=='non_additive','non-additive source facts')
            if failures:mismatch.append({'key':key[:4],'failures':failures})
        assert not mismatch,mismatch[:10]
        assert Counter(actual_keys)==Counter(expected_by_key.keys())
        registered=(await db.execute(text('SELECT relative_path,sha256,byte_size,source_url,kind,metadata AS metadata_ FROM source_artifacts WHERE task_uuid=:task'),{'task':receipt['task_uuid']})).mappings().all()
        assert len(registered)==len(manifest['artifacts'])
        artifact_key=lambda a:(a['sha256'],a['byte_size'],a['source_url'],a['kind'],a['metadata_'].get('endpoint'))
        assert Counter(artifact_key(a) for a in registered)==Counter(artifact_key(a) for a in manifest['artifacts'])
        for artifact in registered:read_artifact(SimpleNamespace(**dict(artifact)))
    await dispose_database()
    assert get_country_bootstrap_config('ID')['public_release_enabled'] is False
    assert receipt['public_release_enabled'] is False
    result={'status':'accepted','verified_at':datetime.now(timezone.utc).isoformat(),
        'task_uuid':receipt['task_uuid'],'crawl_run_id':receipt['crawl_run_id'],
        'csv_rows':len(expected),'database_rows':len(database),'row_by_row_mismatches':0,
        'native_duplicate_identities':len(actual_keys)-len(set(actual_keys)),
        'valued_rows':sum(r['Cases']!='' for r in expected),'source_missing_null_rows':sum(r['Cases']=='' for r in expected),
        'explicit_zero_rows':sum(r['Cases']=='0' for r in expected),'registered_raw_artifacts':len(registered),
        'complete_raw_profile_years':source_audit['report_years'],'source_statistic_years':source_audit['statistic_years'],
        'profile_row_counts':{str(r['year']):r['rows'] for r in source_audit['reports']},
        'measure_counts':dict(Counter(r['Measure'] for r in expected)),
        'public_release_enabled':False,'all_series_unmapped_non_additive_holding':True,
        'source_csv_sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        'raw_manifest':str(manifest_path.relative_to(ROOT)),
        'raw_manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'parser_sha256':source_audit['parser_sha256'],
        'every_row_resolves_to_captured_official_pdf_and_physical_page':True,
        'database_read_only_transaction':True,
        'coverage_note':'Nine complete published books archived; numerical extraction partial. Unsupported/overlapping tables remain audited. Independent prose and appendix values are unreconciled source-native facts.'}
    target=ROOT/'data/current/id/database_acceptance.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
