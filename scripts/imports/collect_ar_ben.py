#!/usr/bin/env python3
"""Download full official BEN archive and export validated source-native tables.

No database writes; normal staged ingestion owns importing these observations.
Cached files are integrity-checked source archives, not a claim of live refresh.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.data.crawlers.ar import ArgentinaBENCrawler, HISTORY_START_YEAR, parse_ben_payload, deduplicate_revisions, _replace_cache


def parse_saved(item):
    ref, path = item
    try:
        return parse_ben_payload(Path(path).read_bytes(), source_url=ref["url"], reference=ref)
    except Exception as exc:
        return [], {"source_url": ref["url"], "status": "parse_failed", "parsed_rows": 0, "error": str(exc)}


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--start-year",type=int,default=HISTORY_START_YEAR);ap.add_argument("--force",action="store_true");ap.add_argument("--workers",type=int,default=4);ap.add_argument("--offline-organize",action="store_true");args=ap.parse_args()
    crawler=ArgentinaBENCrawler();out=crawler.archive_dir.parent;out.mkdir(parents=True,exist_ok=True)
    if args.offline_organize:
        discovery=json.loads((out/'discovery.json').read_text())
        refs=discovery['references'];failures=discovery['failures'];indexes=[]
    else:
        refs,indexes,failures=crawler.discover(start_year=args.start_year)
        (out/'discovery.json').write_text(json.dumps({"references":refs,"failures":failures},ensure_ascii=False,indent=2))
    for url,payload in indexes:
        (crawler.archive_dir/('index-'+hashlib.sha256(url.encode()).hexdigest()+'.html')).parent.mkdir(parents=True,exist_ok=True)
        _replace_cache(crawler.archive_dir/('index-'+hashlib.sha256(url.encode()).hexdigest()+'.html'),payload)
    print(f"Discovered {len(refs)} PDFs; {len(failures)} index failures",flush=True)
    if args.offline_organize:
        # download() reuses validated receipts; require all files in offline mode
        for ref in refs:
            path=crawler.archive_dir/(hashlib.sha256(ref['url'].encode()).hexdigest()+'.pdf')
            if not path.exists() or not path.with_suffix('.json').exists():
                raise ValueError(f"Offline archive missing {ref['url']}")
    saved=[]
    def retrieve(ref):
        if not args.offline_organize:
            return crawler.download(ref,force=args.force)
        path=crawler.archive_dir/(hashlib.sha256(ref['url'].encode()).hexdigest()+'.pdf')
        payload=path.read_bytes();meta=json.loads(path.with_suffix('.json').read_text())
        if payload[:4]!=b'%PDF' or hashlib.sha256(payload).hexdigest()!=meta['sha256']:
            raise ValueError('Offline BEN archive failed integrity verification')
        return payload,{**meta,'cache_reused':True}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fs={pool.submit(retrieve,ref):ref for ref in refs}
        for i,f in enumerate(as_completed(fs),1):
            ref=fs[f]
            try:
                payload,meta=f.result();saved.append((ref,meta['archive_path']))
                print(f"download {i}/{len(refs)} {len(payload)} {ref['label']}",flush=True)
            except Exception as exc:
                failures.append({"url":ref['url'],"stage":"download","error":str(exc)})
                print(f"download failed {ref['url']}: {exc}",flush=True)
    # Prepare a database-ready artifact manifest without downloading twice.
    raw_root=Path('data/raw/ar/ben_full_history');raw_root.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for ref,path in saved:
        original=Path(path);meta=json.loads(original.with_suffix('.json').read_text())
        target=raw_root/(original.stem+'-'+meta['sha256'][:16]+'.pdf')
        if not target.exists():
            os.link(original,target)
        manifest.append({'kind':'ar_ben_pdf','source_url':ref['url'],'relative_path':str(target),'sha256':meta['sha256'],'byte_size':meta['byte_size'],'metadata_':meta})
    index_urls={'https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional'}|{ref['index_url'] for ref in refs}
    for url in sorted(index_urls):
        original=crawler.archive_dir/('index-'+hashlib.sha256(url.encode()).hexdigest()+'.html')
        if original.exists():
            payload=original.read_bytes()
            target=raw_root/(original.stem+'-'+hashlib.sha256(payload).hexdigest()[:16]+'.html')
            if not target.exists():os.link(original,target)
            manifest.append({'kind':'ar_ben_index','source_url':url,'relative_path':str(target),'sha256':hashlib.sha256(payload).hexdigest(),'byte_size':len(payload),'metadata_':{'role':'archive_discovery'}})
    plan={'references':refs,'failures':failures,'downloaded_reports':len(saved),'requested_start_year':args.start_year}
    payload=json.dumps(plan,ensure_ascii=False).encode();plan_path=raw_root/('collection-plan-'+hashlib.sha256(payload).hexdigest()+'.json')
    if not plan_path.exists():plan_path.write_bytes(payload)
    manifest.append({'kind':'ar_ben_plan','source_url':'https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional','relative_path':str(plan_path),'sha256':hashlib.sha256(payload).hexdigest(),'byte_size':len(payload),'metadata_':{'role':'collection_plan'}})
    (out/'artifacts_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    (raw_root/'artifacts_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    rows=[];reports=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i,(parsed,audit) in enumerate(pool.map(parse_saved,saved),1):
            rows.extend(parsed);reports.append(audit)
            print(f"parse {i}/{len(saved)} rows={len(parsed)} {audit.get('report_number',audit.get('error',''))}",flush=True)
    rows=deduplicate_revisions(rows)
    if rows:
        with (out/'argentina_ben_weekly.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    audit={"discovered_reports":len(refs),"archived_reports":len(saved),"parsed_reports":sum(bool(x['parsed_rows']) for x in reports),"rows":len(rows),"categories":len({r['SourceDiseaseCode'] for r in rows}),"date_min":min((r['Date'] for r in rows),default=None),"date_max":max((r['Date'] for r in rows),default=None),"failures":failures,"reports":reports,"status":"partial" if failures or any(not x['parsed_rows'] for x in reports) else "validated_supported_tables"}
    (out/'source_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in audit.items() if k not in {'reports','failures'}},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
