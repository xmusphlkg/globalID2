#!/usr/bin/env python3
"""Download actual linked national DGE annual editions and organize offline.

No database writes. Cache bytes are immutable and checksum verified. Source
request failures and unsupported PDF schemas remain explicit in the audit.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,os,sys
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.data.crawlers.mx import MexicoDGEAnnualCrawler,HISTORY_START_YEAR,parse_national_pdf,LANDING_URL
RAW=Path('data/raw/mx/dge_annual_full_history')
OUT=Path('data/current/mx')

def immutable(payload,extension):
    digest=hashlib.sha256(payload).hexdigest();path=RAW/(digest+'.'+extension)
    if path.exists():
        if path.read_bytes()!=payload:raise ValueError('Content addressed DGE cache collision')
    else:
        temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(payload);os.replace(temporary,path)
    return path,digest

def artifact(payload,*,url,kind,metadata,extension):
    path,digest=immutable(payload,extension)
    return {'kind':kind,'source_url':url,'relative_path':str(path),'sha256':digest,'byte_size':len(payload),'metadata_':metadata}

def parse_saved(item):
    ref,record=item
    path=Path(record['relative_path']);payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=record['sha256'] or len(payload)!=record['byte_size']:raise ValueError('DGE artifact integrity failure')
    try:return parse_national_pdf(payload,reference=ref,retrieved_at=record['metadata_'].get('retrieved_at'))
    except Exception as exc:return [],{'source_url':ref['url'],'year':ref['year'],'sex':ref['sex'],'table':ref['table'],'status':'parse_failed','rows':0,'error':str(exc)}

def crawler_channel_url():
    from src.data.crawlers.mx import NATIONAL_URL
    return NATIONAL_URL

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start-year',type=int,default=HISTORY_START_YEAR);ap.add_argument('--end-year',type=int);ap.add_argument('--workers',type=int,default=4);ap.add_argument('--force',action='store_true');ap.add_argument('--offline-organize',action='store_true');args=ap.parse_args()
    RAW.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    crawler=MexicoDGEAnnualCrawler();manifest=[];errors=[]
    old=json.loads((RAW/'artifacts_manifest.json').read_text()) if (RAW/'artifacts_manifest.json').exists() else []
    known={r['source_url']:r for r in old if r['kind']=='mx_dge_pdf'}
    if args.offline_organize:
        manifests=old
        plans=[x for x in old if x['kind']=='mx_dge_plan']
        if len(plans)!=1:raise ValueError('Offline organize needs one recorded collection plan')
        for record in old:
            body=Path(record['relative_path']).read_bytes()
            if len(body)!=record['byte_size'] or hashlib.sha256(body).hexdigest()!=record['sha256']:raise ValueError('Offline DGE archive integrity failure')
        plan=json.loads(Path(plans[0]['relative_path']).read_bytes());refs=plan['references'];errors=plan['errors'];manifest=[x for x in old if x['kind']=='mx_dge_index']
    else:
        refs,indexes=crawler.discover(start_year=args.start_year,end_year=args.end_year)
        for url,body in indexes:manifest.append(artifact(body,url=url,kind='mx_dge_index',metadata={'role':'national_discovery'},extension='html'))
    print(f'Discovered {len(refs)} national source tables',flush=True)
    def download(ref):
        record=known.get(ref['url'])
        if record and not args.force:
            body=Path(record['relative_path']).read_bytes()
            if len(body)!=record['byte_size'] or hashlib.sha256(body).hexdigest()!=record['sha256']:raise ValueError('DGE cached PDF checksum differs')
            age=datetime.now(timezone.utc)-datetime.fromisoformat(record['metadata_']['retrieved_at'])
            if args.offline_organize or age<timedelta(days=7):return record
        if args.offline_organize:raise ValueError('DGE offline artifact missing')
        body=crawler.get(ref['url']).content
        if not body.startswith(b'%PDF'):raise ValueError('DGE returned non-PDF response')
        return artifact(body,url=ref['url'],kind='mx_dge_pdf',metadata={**ref,'retrieved_at':datetime.now(timezone.utc).isoformat()},extension='pdf')
    saved=[]
    # Recorded source failures remain gaps during offline replay. A missing
    # artifact that the plan claimed to capture is an integrity failure.
    scheduled=[ref for ref in refs if not args.offline_organize or ref['url'] in known]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(download,ref):ref for ref in scheduled}
        for n,f in enumerate(as_completed(futures),1):
            ref=futures[f]
            try:
                record=f.result();manifest.append(record);saved.append((ref,record));print(f'capture {n}/{len(refs)} {ref["year"]} {ref["sex"]} {ref["table"]} {record["byte_size"]}',flush=True)
            except Exception as exc:
                if args.offline_organize:raise
                errors.append({'url':ref['url'],'stage':'download','error':str(exc)});print(f'capture failed {ref["url"]}: {exc}',flush=True)
    saved.sort(key=lambda pair:(pair[0]['year'],pair[0]['sex'],pair[0]['table']))
    plan={'references':refs,'captured':[{'url':r['source_url'],'sha256':r['sha256']} for _,r in saved],'errors':errors,'requested_start_year':args.start_year,'requested_end_year':args.end_year}
    manifest.append(artifact(json.dumps(plan,ensure_ascii=False).encode(),url=LANDING_URL,kind='mx_dge_plan',metadata={'role':'collection_plan'},extension='json'))
    manifest.sort(key=lambda item:(item['kind'],item['source_url']))
    manifest_tmp=RAW/'artifacts_manifest.json.tmp';manifest_tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');os.replace(manifest_tmp,RAW/'artifacts_manifest.json')
    rows=[];reports=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n,(parsed,audit) in enumerate(pool.map(parse_saved,saved),1):
            rows.extend(parsed);reports.append(audit);print(f'parse {n}/{len(saved)} {audit["year"]} {audit["sex"]} rows={len(parsed)} {audit.get("error","")}',flush=True)
    rows.sort(key=lambda r:(r['Date'],r['SourceDiseaseCode']))
    if len({(r['Date'],r['SourceDiseaseCode']) for r in rows})!=len(rows):raise ValueError('Duplicate DGE normalized source cell')
    if rows:
        with (OUT/'mexico_dge_annual.csv').open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    audit={'discovered_tables':len(refs),'archived_tables':len(saved),'parsed_tables':sum(bool(r['rows']) for r in reports),'fully_parsed_tables':sum(r['status']=='parsed' for r in reports),'rows':len(rows),'numeric_rows':sum(bool(r['Cases']) for r in rows),'missing_rows':sum(not r['Cases'] for r in rows),'source_identities':len({r['SourceDiseaseCode'] for r in rows}),'date_min':min((r['Date'] for r in rows),default=None),'date_max':max((r['Date'] for r in rows),default=None),'years':sorted({int(r['Year']) for r in rows}),'errors':errors,'reports':reports,'status':'partial' if errors or any(r['status']!='parsed' for r in reports) else 'all_linked_national_age_tables_parsed','excluded':'State tables, institution strata, and monthly distributions are not normalized; 2002 institution table contributes only its explicit national total.'}
    audit.update({'failed_count':len(errors)+sum(r.get('failed_count',1 if r['status']=='parse_failed' else 0) for r in reports),'coverage_complete':not errors and all(r['status']=='parsed' for r in reports),'channel_url':crawler_channel_url(),'archive_url':LANDING_URL,'parser_entrypoint':'src.data.crawlers.mx.discover_national_tables','cache_ttl_days':7,'raw_manifest':str(RAW/'artifacts_manifest.json')})
    (OUT/'source_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in audit.items() if k not in {'reports','errors'}},ensure_ascii=False),flush=True)
    crawler.session.close()
if __name__=='__main__':main()
