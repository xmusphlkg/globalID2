#!/usr/bin/env python3
"""Capture all live official NICD NMC channels, then organize verified originals."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import requests
from src.data.crawlers.za import INDEX_URL,MEDIA_URLS,discover_reports,discover_media
from src.data.processors.za import SouthAfricaNICDNMCUpdater,write_review_export
from src.services.ingestion.countries.za import organize_documents

def write_json(path,value):
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)

def capture_archive(directory,*,resume=False,workers=4):
 directory.mkdir(parents=True,exist_ok=True);previous={r['url']:r for r in json.loads((directory/'capture_manifest.json').read_text())} if (directory/'capture_manifest.json').exists() else {}
 def fetch(url,*,catalogue=False):
  if resume and not catalogue and url in previous:
   r=previous[url]
   if r.get('path'):
    payload=Path(r['path']).read_bytes()
    if len(payload)==r['bytes'] and hashlib.sha256(payload).hexdigest()==r['sha256']:return payload,r,{}
  for attempt in range(3):
   try:
    response=requests.get(url,timeout=(15,65));response.raise_for_status();payload=response.content
    if not payload:raise ValueError('Empty official response')
    sha=hashlib.sha256(payload).hexdigest();suffix='.pdf' if payload.startswith(b'%PDF') else '.docx' if payload.startswith(b'PK') else '.html' if url==INDEX_URL else '.json'
    path=directory/(hashlib.sha256(url.encode()).hexdigest()+'_'+sha+suffix);path.write_bytes(payload)
    receipt={'url':url,'path':str(path),'sha256':sha,'bytes':len(payload),'retrieved_at':datetime.now(timezone.utc).isoformat(),'etag':response.headers.get('ETag'),'last_modified':response.headers.get('Last-Modified')};write_json(path.with_suffix(path.suffix+'.receipt.json'),receipt)
    return payload,receipt,response.headers
   except (requests.RequestException,ValueError):
    if attempt==2:raise
 payload,receipt,_=fetch(INDEX_URL,catalogue=True);reports=discover_reports(payload);catalogues=[receipt];failures=[]
 for base in MEDIA_URLS:
  page=1;pages=1
  while page<=pages:
   url=base if page==1 else base+f'&page={page}'
   try:
    payload,receipt,headers=fetch(url,catalogue=True);reports=discover_media(payload,reports);pages=int(headers.get('X-WP-TotalPages','1'));catalogues.append(receipt|{'total_pages':pages})
   except Exception as exc:failures.append({'url':url,'kind':'catalogue','error':str(exc)});break
   page+=1
 write_json(directory/'discovered.json',reports);write_json(directory/'catalogue_audit.json',catalogues+failures)
 def download(report):
  try:
   payload,receipt,_=fetch(report['url'])
   if not(payload.startswith(b'%PDF') or payload.startswith(b'PK')):raise ValueError('Official report response not PDF/DOCX')
   return report|receipt
  except Exception as exc:return report|{'error':str(exc)}
 with ThreadPoolExecutor(max_workers=workers) as pool:captured=list(pool.map(download,reports))
 write_json(directory/'capture_manifest.json',captured);print(f'[capture] {sum("path" in r for r in captured)}/{len(captured)} reports',flush=True)
 return captured

def export_artifacts(directory):
 reports=json.loads((directory/'capture_manifest.json').read_text());catalogue_path=directory/'catalogue_audit.json';catalogues=json.loads(catalogue_path.read_text()) if catalogue_path.exists() else []
 plan={'reports':reports,'failed_downloads':[r for r in reports if r.get('error')]+[r for r in catalogues if r.get('error')],'catalogue_audit':catalogues,'mode':'explicit_verified_archive_replay','channel_url':INDEX_URL,'archive_url':INDEX_URL,'discovery_endpoints':[INDEX_URL,*MEDIA_URLS]};plan_path=directory/'organization_plan.json';write_json(plan_path,plan)
 artifacts=[]
 def artifact(path,url,kind,metadata):
  payload=path.read_bytes();return {'kind':kind,'source_url':url,'relative_path':path.as_posix(),'sha256':hashlib.sha256(payload).hexdigest(),'byte_size':len(payload),'metadata_':metadata}
 for r in reports:
  if r.get('error'):continue
  path=Path(r['path']);payload=path.read_bytes()
  if len(payload)!=r['bytes'] or hashlib.sha256(payload).hexdigest()!=r['sha256']:raise ValueError(f'Captured raw integrity failed: {path}')
  artifacts.append(artifact(path,r['url'],'za_nicd_report',{'report':r,'recovery':True,'retrieved_at':r.get('retrieved_at')}))
 for r in catalogues:
  if r.get('path') and not r.get('error'):artifacts.append(artifact(Path(r['path']),r['url'],'za_nicd_index',{'recovery':True}))
 # Initial complete capture can predate catalogue receipt export.
 if not catalogues:
  for name,url in [('catalogue.html',INDEX_URL),('wp_media_0.json',MEDIA_URLS[0]),('wp_media_1.json',MEDIA_URLS[1])]:
   p=directory/name
   if p.exists():artifacts.append(artifact(p,url,'za_nicd_index',{'recovery':True}))
 artifacts.append(artifact(plan_path,INDEX_URL,'za_nicd_plan',{'recovery':True}));write_json(directory/'artifacts_manifest.json',artifacts);return reports,plan

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,default=Path('data/raw/za/nicd_nmc_full_history'));p.add_argument('--download',action='store_true');p.add_argument('--resume',action='store_true',help='Explicit recovery of integrity-verified report originals; catalogues still fetched live');p.add_argument('--workers',type=int,default=4);p.add_argument('--organize',action='store_true');o=p.parse_args()
 if o.download:capture_archive(o.directory,resume=o.resume,workers=o.workers)
 reports,plan=export_artifacts(o.directory)
 if o.organize:
  def documents():
   for r in reports:
    if r.get('error'):continue
    payload=Path(r['path']).read_bytes()
    if len(payload)!=r['bytes'] or hashlib.sha256(payload).hexdigest()!=r['sha256']:raise ValueError('Source integrity failed')
    yield r,payload
  rows,audit=organize_documents(documents(),expected_reports=reports,failed_downloads=plan['failed_downloads']);audit.update(collection_mode=plan['mode'],catalogue_audit=plan['catalogue_audit']);write_review_export(SouthAfricaNICDNMCUpdater().prepare_rows(rows,source_audit=audit));print(f'[organized] {len(rows)} observations; parsed {audit["parsed_reports"]}; unparsed {audit["unparsed_reports"]}',flush=True)
if __name__=='__main__':main()
