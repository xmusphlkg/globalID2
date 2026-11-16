#!/usr/bin/env python3
"""Archive, review and apply evidence-backed Chinese province backfills.

Collect only captures official responses and candidates. Plan compares them
with a consistent database snapshot; apply verifies that reviewed old values
have not changed, retains immutable observation revisions, and marks scopes
for publication. No missing cell is synthesized as zero.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / 'data/processed/cn_province_repair_20261116'


def read_json(path):
    if str(path).endswith('.gz'):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    if str(path).endswith('.gz'):
        with gzip.open(temporary, 'wt', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, default=str)
    else:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def identity(row):
    return (str(row['time'])[:10], row['series_code'], row['geography_key'], row.get('dimension_key', 'all'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def same_value(left, right):
    if left is None or right is None:
        return left is None and right is None
    return Decimal(str(left)) == Decimal(str(right))


async def snapshot(output):
    import asyncpg
    from src.core.config import get_config
    connection = await asyncpg.connect(get_config().database.url.replace('postgresql+asyncpg://', 'postgresql://'), timeout=20)
    try:
        async with connection.transaction(readonly=True, isolation='repeatable_read'):
            await connection.execute("SET LOCAL statement_timeout='120s'")
            rows = await connection.fetch("""
                SELECT o.*, s.source_system, s.source_label, s.metric_type
                FROM disease_series_observations o
                JOIN disease_surveillance_series s USING(series_code)
                WHERE o.geography_key LIKE 'country:CN-%:national'
                  AND s.source_system='SRC_CN_PROV_MONTHLY_REPORT'
                ORDER BY o.geography_key,o.time,o.series_code,o.dimension_key
            """)
            facts = []
            for r in rows:
                item = dict(r)
                for key in ('raw_data', 'metadata', 'dimensions'):
                    if isinstance(item.get(key), str):
                        item[key] = json.loads(item[key])
                facts.append(item)
            guard = [dict(r) for r in await connection.fetch("""
                SELECT o.geography_key,s.source_system,count(*) observations,
                       sum(o.value) total_value,min(o.time) first_date,max(o.time) last_date
                FROM disease_series_observations o JOIN disease_surveillance_series s USING(series_code)
                WHERE o.geography_key='country:CN:national'
                   OR (o.geography_key LIKE 'country:CN-%:national' AND s.source_system='SRC_CN_PROV_DATACENTER')
                GROUP BY 1,2 ORDER BY 1,2
            """)]
        result = {'created_at': utc_now(), 'observation_count': len(facts), 'observations': facts, 'protected_scope_totals': guard}
        write_json(output / 'before.json.gz', result)
        print(json.dumps({'snapshot': str(output/'before.json.gz'), 'observations': len(facts)}, ensure_ascii=False), flush=True)
    finally:
        await connection.close()


def make_session(folder):
    import requests
    class ArchivedSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.records = []
            self.folder = folder
            folder.mkdir(parents=True, exist_ok=True)
            manifest = folder/'manifest.json'
            self.cache = {r['key']: r for r in read_json(manifest).get('responses', [])} if manifest.exists() else {}
        def request(self, method, url, **kwargs):
            key = digest([method.upper(), url, kwargs.get('params'), kwargs.get('data')])
            cached = self.cache.get(key)
            if cached and cached['status_code'] < 400:
                content = (folder / cached['file']).read_bytes()
                if hashlib.sha256(content).hexdigest() != cached['sha256']:
                    raise ValueError('Cached official response hash mismatch')
                response = requests.Response()
                response.status_code = cached['status_code'];response.url = cached['response_url']
                response.headers.update(cached['headers']);response.encoding = cached['encoding']
                response._content = content;response._content_consumed = True
                return response
            response = super().request(method, url, **kwargs)
            content = response.content
            sha = hashlib.sha256(content).hexdigest(); filename = sha+'.bin'
            (folder/filename).write_bytes(content)
            record = {'key':key,'method':method.upper(),'url':url,'params':kwargs.get('params'),
                      'data':kwargs.get('data'),'status_code':response.status_code,'response_url':response.url,
                      'headers':{'Content-Type':response.headers.get('Content-Type','')},'encoding':response.encoding,
                      'sha256':sha,'file':filename,'retrieved_at':utc_now()}
            self.cache[key]=record
            write_json(folder/'manifest.json',{'responses':list(self.cache.values())})
            return response
    return ArchivedSession()


def collect_province(code, output, baseline, start_year, limit):
    from src.data.crawlers.cn_provinces import ProvinceMonthlyReportCrawler, MonthlyReportLink, province_configs
    from src.services.cn_province_health import parser_fingerprint
    result={'province':code,'started_at':utc_now(),'reports':[],'errors':[],
            'source_profile_sha256':digest(asdict(province_configs()[code])),
            'parser_sha256':parser_fingerprint()}
    with make_session(output/'raw'/code) as session:
        crawler=ProvinceMonthlyReportCrawler(session=session,timeout=15)
        try:
            links=crawler.discover(code)
            result['discovered']=len(links)
            if hasattr(crawler,'discovery_diagnostics'):result['discovery_diagnostics']=crawler.discovery_diagnostics
        except Exception as exc:
            links=[];result['errors'].append({'stage':'discover','error':str(exc)})
        # Re-read previously stored narrative documents, including pages which
        # have fallen off the current source's first page.
        for old in baseline:
            raw=old.get('raw_data') or {}
            if 'explicit_narrative_value' not in (raw.get('Raw') or {}):continue
            url=raw.get('SourceURL')
            if url:
                links.append(MonthlyReportLink(code,date.fromisoformat(str(old['time'])[:10]),
                                               str((raw.get('Raw') or {}).get('title') or ''),url))
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        unique={(l.report_date,l.url):l for l in links if l.report_date.year>=start_year and l.report_date<today.replace(day=1)}
        selected=sorted(unique.values(),key=lambda l:(l.report_date,l.url))
        if limit:selected=selected[-limit:]
        result['selected']=len(selected)
        for link in selected:
            report={'date':link.report_date.isoformat(),'url':link.url,'title':link.title}
            try:
                parsed=crawler.fetch(link)
                images=getattr(parsed,'image_documents',[])
                report.update(status='needs_review' if images else 'parsed',rows=parsed.rows,source_url=parsed.source_url,
                              artifact_sha256=parsed.artifact_sha256,content_type=parsed.content_type,
                              retrieved_at=parsed.retrieved_at)
                report['image_documents']=[{key:doc[key] for key in ('source_url','sha256','media_type')} for doc in images]
                report['evidence_type']='narrative_sparse' if all('explicit_narrative_value' in r.get('Raw',{}) for r in parsed.rows) else 'table'
            except Exception as exc:
                report.update(status='failed',error=str(exc));result['errors'].append({'stage':'fetch','date':report['date'],'url':link.url,'error':str(exc)})
            report['diagnostics']=list(getattr(crawler,'report_diagnostics',[]))
            result['reports'].append(report)
            write_json(output/'provinces'/(code+'.json'),result)
        result['finished_at']=utc_now()
        write_json(output/'provinces'/(code+'.json'),result)
    return {k:v for k,v in result.items() if k!='reports'} | {'parsed_reports':sum(r['status']=='parsed' for r in result['reports']),
                                                              'rows':sum(len(r.get('rows',[])) for r in result['reports'])}


def collect(output, provinces, start_year, workers, limit):
    from src.data.crawlers.cn_provinces import province_configs
    baseline=read_json(output/'before.json.gz')['observations']
    by_province=defaultdict(list)
    for row in baseline:by_province[row['geography_key'].split(':')[1]].append(row)
    selected=provinces or list(province_configs())
    summaries=[]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks={executor.submit(collect_province,c,output,by_province[c],start_year,limit):c for c in selected}
        for task in as_completed(tasks):
            try:summary=task.result()
            except Exception as exc:summary={'province':tasks[task],'fatal_error':str(exc)}
            summaries.append(summary);print(json.dumps(summary,ensure_ascii=False,default=str),flush=True)
            write_json(output/'collection_summary.json',sorted(summaries,key=lambda s:s['province']))


def build_plan(output, *, insert_only=False):
    from src.data.processors.cn_provinces import expand_province_metric_rows
    from src.data.storage.series_observation_store import SeriesObservationStore
    before=read_json(output/'before.json.gz');by_identity={identity(r):r for r in before['observations']}
    old_by_month=defaultdict(list)
    for row in before['observations']:old_by_month[(row['geography_key'].split(':')[1],str(row['time'])[:10])].append(row)
    store=SeriesObservationStore();changes=[];errors=[];noops=0;reparsed=0
    for path in sorted((output/'provinces').glob('CN-*.json')):
        province=read_json(path);code=province['province'];by_month=defaultdict(list)
        for report in province['reports']:
            if report['status']=='parsed':by_month[report['date']].append(report)
        # Reviewed image tables supplement sparse prose with image provenance.
        for image_path in sorted((output/'image_review').glob(f'{code}_*.json')):
            review=read_json(image_path)
            if review.get('status')!='approved_for_import':continue
            by_month[review['report_month']+'-01'].append({
                'date':review['report_month']+'-01','url':review['official_report_url'],
                'rows':review['rows'],'evidence_type':'reviewed_image_table'})
        for month,reports in sorted(by_month.items()):
            candidates={};conflicts=[];validated_document_urls=set()
            for report in reports:
                if any(row.get('SourceID')!='SRC_CN_PROV_MONTHLY_REPORT'
                       or row.get('JurisdictionCode')!=code
                       or row.get('GeographyKey')!=f'country:{code}:national'
                       or row.get('Date')!=month for row in report['rows']):
                    errors.append({'province':code,'month':month,'url':report['url'],'reason':'source_report_scope_mismatch'})
                    continue
                expanded=expand_province_metric_rows(report['rows'])
                built=store.build_observations(expanded,'CN',source_id='SRC_CN_PROV_MONTHLY_REPORT')
                if built.skipped_unmatched or built.skipped_invalid or built.skipped_ambiguous:
                    errors.append({'province':code,'month':month,'url':report['url'],'reason':'registry_preflight_failed',
                                   'unmatched':built.skipped_unmatched,'invalid':built.skipped_invalid,'ambiguous':built.skipped_ambiguous})
                    continue
                validated_document_urls.add(report['url'])
                # Resolve duplicate official reports only when values agree.
                for observation in built.observations:
                    raw=observation['raw_data']
                    key=identity(observation)
                    previous=candidates.get(key)
                    if previous and not same_value(previous['value'],observation['value']):conflicts.append({'identity':key,'values':[previous['value'],observation['value']]})
                    else:candidates[key]={'raw':raw,'value':observation['value'],'evidence_type':report['evidence_type']}
            if conflicts:
                errors.append({'province':code,'month':month,'reason':'conflicting_official_documents','conflicts':conflicts});continue
            if not candidates:continue
            if insert_only:
                review_conflicts=[(key,candidate,by_identity[key]) for key,candidate in candidates.items()
                                  if key in by_identity and not same_value(by_identity[key]['value'],candidate['value'])]
                if review_conflicts:
                    errors.extend({'province':code,'month':month,'reason':'existing_observation_requires_review',
                                   'identity':key,'old_value':old['value'],'candidate_value':candidate['value'],
                                   'url':candidate['raw'].get('SourceURL')}
                                  for key,candidate,old in review_conflicts)
                    continue
            reparsed+=1
            for key,candidate in candidates.items():
                old=by_identity.get(key);raw=candidate['raw'];operation='insert' if old is None else 'update'
                old_evidence=((old or {}).get('raw_data') or {}).get('Raw') or {}
                new_evidence=raw.get('Raw') or {}
                evidence_changed=('explicit_narrative_value' in old_evidence and (
                    old_evidence.get('explicit_narrative_value')!=new_evidence.get('explicit_narrative_value')
                    or ((old or {}).get('raw_data') or {}).get('SourceURL')!=raw.get('SourceURL')))
                if old is not None and (insert_only or (same_value(old['value'],candidate['value']) and not evidence_changed)):
                    noops+=1;continue
                raw={**raw,'AuthoritativeRevision':old is not None,'QualityStatus':'revised' if old else raw.get('QualityStatus','raw'),
                     'RepairReason':'cn_province_evidence_reparse_20261116'}
                if old is not None:raw['DataStatus']='revised'
                changes.append({'identity':key,'operation':operation,'old_value':old['value'] if old else None,
                                'new_value':candidate['value'],'old_row_hash':digest(old) if old else None,
                                'evidence_type':candidate['evidence_type'],'row':raw})
            for old in old_by_month[(code,month)]:
                if insert_only:
                    continue
                key=identity(old)
                if key in candidates or old['value'] is None:continue
                raw=old.get('raw_data') or {}
                if 'explicit_narrative_value' not in (raw.get('Raw') or {}):continue
                # Absence only withdraws a prior sparse narrative extraction,
                # never an absent cell from a historical table.
                document_urls=validated_document_urls
                if raw.get('SourceURL') not in document_urls:continue
                # A table may omit a disease that the same page explicitly
                # lists as zero in prose. Reparse the retained evidence before
                # authorizing a withdrawal, rather than trusting absence from
                # the table parser's first successful result.
                from src.data.crawlers.cn_provinces import MonthlyReportLink, _normalize_narrative_report
                evidence=raw['Raw']['explicit_narrative_value']
                retained=_normalize_narrative_report(evidence,
                    link=MonthlyReportLink(code,date.fromisoformat(month),str(raw['Raw'].get('title') or ''),raw['SourceURL']),
                    source_url=raw['SourceURL'],artifact_sha256=str(raw['Raw'].get('artifact_sha256') or ''))
                if any(row['SourceDiseaseCode']==raw.get('SourceDiseaseCode')
                       and same_value(row['Cases'],old['value']) for row in retained):
                    continue
                if not same_value(old['value'],0):
                    errors.append({'province':code,'month':month,'reason':'positive_narrative_requires_manual_review',
                                   'series_code':old['series_code'],'value':old['value']})
                    continue
                correction={**raw,'Cases':None,'MissingValueStatus':'source_missing','QualityStatus':'revised',
                            'DataStatus':'revised',
                            'AuthoritativeRevision':True,'RepairReason':'previous_narrative_value_not_supported_by_reparsed_official_report',
                            'Raw':{**raw['Raw'],'previous_value':old['value'],'reparsed_document_urls':sorted(document_urls),
                                   'repair_verified_at':utc_now()}}
                changes.append({'identity':key,'operation':'withdraw_unsupported_narrative','old_value':old['value'],'new_value':None,
                                'old_row_hash':digest(old),'evidence_type':'narrative_sparse','row':correction})
    result={'created_at':utc_now(),'snapshot_sha256':hashlib.sha256((output/'before.json.gz').read_bytes()).hexdigest(),
            'reparsed_province_months':reparsed,'noops':noops,'counts':dict(Counter(c['operation'] for c in changes)),
            'provinces':sorted({c['identity'][2].split(':')[1] for c in changes}),'errors':errors,'changes':changes}
    write_json(output/'plan.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='changes'},ensure_ascii=False),flush=True)
    return result


def validate_plan_changes(plan, before, store):
    from src.data.crawlers.cn_provinces import province_configs
    allowed_provinces=province_configs()
    seen=set()
    for change in plan['changes']:
        key=tuple(change['identity']);old=before.get(key)
        if key in seen:raise ValueError('Duplicate planned observation identity')
        seen.add(key)
        if change.get('old_row_hash')!=(digest(old) if old is not None else None):
            raise ValueError('Planned old row hash does not match the reviewed snapshot')
        if not same_value(change.get('old_value'),old['value'] if old else None):
            raise ValueError('Planned old value does not match the reviewed snapshot')
        if (change['operation']=='insert')!=(old is None):
            raise ValueError('Planned operation does not match the reviewed snapshot')
        if change['operation'] not in {'insert','update','withdraw_unsupported_narrative'}:
            raise ValueError('Unrecognized repair operation')
        built=store.build_observations([change['row']],'CN',source_id='SRC_CN_PROV_MONTHLY_REPORT')
        if (built.skipped_unmatched or built.skipped_invalid or built.skipped_ambiguous
                or len(built.observations)!=1 or identity(built.observations[0])!=key
                or not same_value(built.observations[0]['value'],change['new_value'])):
            raise ValueError('Planned row does not match its reviewed observation identity or value')
        code=key[2].split(':')[1]
        if (code not in allowed_provinces
                or change['row'].get('SourceID')!='SRC_CN_PROV_MONTHLY_REPORT'
                or change['row'].get('JurisdictionCode')!=code
                or key[2]!=f'country:{code}:national'):
            raise ValueError('Planned source or province is outside the repair scope')


async def apply_plan(output):
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from src.core.database import get_db,dispose_database
    from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
    from src.domain import DiseaseSeriesObservation as Observation, DiseaseSeriesObservationRevision as Revision
    from src.data.processors.cn_provinces import CNProvinceUpdater
    from src.services.ingestion.stages import mark_dirty
    plan=read_json(output/'plan.json')
    if hashlib.sha256((output/'before.json.gz').read_bytes()).hexdigest()!=plan['snapshot_sha256']:
        raise ValueError('Reviewed baseline snapshot changed')
    before={identity(r):r for r in read_json(output/'before.json.gz')['observations']}
    store_updater=CNProvinceUpdater()
    validate_plan_changes(plan,before,store_updater.store)
    groups=defaultdict(list)
    for c in plan['changes']:groups[c['identity'][2].split(':')[1]].append(c)
    receipt={'started_at':utc_now(),'plan_sha256':hashlib.sha256((output/'plan.json').read_bytes()).hexdigest(),'provinces':[]}
    try:
        for code,changes in sorted(groups.items()):
            async with get_db() as db:
                await acquire_country_data_mutation_lock(db,'CN')
                existing=(await db.execute(select(Observation.__table__).where(Observation.geography_key==f'country:{code}:national',
                          Observation.series_code.in_({c['identity'][1] for c in changes})))).mappings().all()
                current={identity(dict(r)):dict(r) for r in existing}
                pending=[];preserved=[];already=0
                for change in changes:
                    key=tuple(change['identity']);old=before.get(key);now=current.get(key)
                    # A restart may encounter an already committed province.
                    if now and same_value(now['value'],change['new_value']) and (now.get('raw_data') or {}).get('RepairPlanSHA256')==receipt['plan_sha256']:
                        already+=1;continue
                    if (old is None)!=(now is None) or (old and (not same_value(now['value'],old['value']) or now['updated_at'].__str__()!=old['updated_at'])):
                        raise RuntimeError(f'Concurrent change after preflight: {key}')
                    if now is not None:
                        # Evidence-only repairs also need the exact previous
                        # raw document state, even if the generic ledger has
                        # an older equal-valued snapshot for this identity.
                        preserved.append({**{field:now[field] for field in (
                            'time','series_code','geography_key','dimension_key','value','unit',
                            'suppressed','suppression_reason','quality_status','dimensions','raw_data','metadata')},
                            'recorded_at':datetime.now(timezone.utc)})
                    # Plan contains explicit metric-expanded rows. Avoid the
                    # updater expanding the same death column twice.
                    row=dict(change['row']);row.pop('Deaths',None)
                    row['RepairPlanSHA256']=receipt['plan_sha256'];pending.append(row)
                if pending:
                    if preserved:
                        await db.execute(pg_insert(Revision.__table__),preserved)
                    result=await store_updater.import_rows(db,pending)
                    if result.skipped_rows:raise RuntimeError(f'Unexpected skipped repair observations: {result.skipped_rows}')
                    await mark_dirty(db,line='surveillance',scope_key=code,task_uuid='cn-province-repair-20261116')
                    applied=asdict(result)
                else:applied={'inserted_or_updated':0,'skipped_rows':0}
                await db.commit()
            receipt['provinces'].append({'province':code,'planned':len(changes),'already_applied':already,**applied})
            write_json(output/'apply_receipt.json',receipt)
            print(json.dumps(receipt['provinces'][-1],ensure_ascii=False,default=str),flush=True)
        receipt['finished_at']=utc_now();write_json(output/'apply_receipt.json',receipt)
    finally:
        await dispose_database()
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['snapshot','collect','plan','apply'])
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--province',action='append',dest='provinces')
    parser.add_argument('--start-year',type=int,default=2022)
    parser.add_argument('--workers',type=int,choices=range(1,7),default=4)
    parser.add_argument('--limit-per-province',type=int,default=0)
    parser.add_argument('--insert-only',action='store_true',help='Plan new identities only; record existing-value differences for review')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if args.command=='snapshot':
        if (args.output/'before.json.gz').exists():raise RuntimeError('Baseline already exists; use a different output directory to take a new snapshot')
        asyncio.run(snapshot(args.output))
    elif args.command=='collect':collect(args.output,args.provinces,args.start_year,args.workers,args.limit_per_province)
    elif args.command=='plan':build_plan(args.output,insert_only=args.insert_only)
    else:asyncio.run(apply_plan(args.output))


if __name__=='__main__':main()
