"""Capture the live Indonesian Health Profile archive and replay verified facts.

Raw persistence precedes organization. Database registration/import is delegated
to import_surveillance_archive.py. No stale source snapshot substitutes for a
failed live request. --replay-manifest is explicitly offline.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from src.data.crawlers.id import HISTORY_START_YEAR
from src.data.processors.id import IndonesiaKemkesProfileUpdater
from src.services.ingestion.countries.id import collect, organize
from src.services.ingestion.artifacts import read_artifact


def merge_retry_captures(manifest_path, retry_paths, directory):
    """Join explicit successful live retries without altering original evidence."""
    import hashlib
    from src.data.crawlers.id import discover_profiles, discover_downloads
    primary=json.loads(Path(manifest_path).read_text())
    original=[SimpleNamespace(**a) for a in primary['artifacts']]
    audits=[a for a in original if a.metadata_.get('endpoint')=='collection_audit']
    if len(audits)!=1: raise ValueError('One primary collection audit required')
    collection=json.loads(read_artifact(audits[0])); active=[a for a in original if a is not audits[0]]
    for retry_path in retry_paths:
        records=[SimpleNamespace(**a) for a in json.loads(Path(retry_path).read_text())['artifacts']]
        retry_audits=[a for a in records if a.metadata_.get('endpoint')=='collection_audit']
        indices=[a for a in records if a.metadata_.get('endpoint')=='index']
        if len(retry_audits)!=1 or len(indices)!=1: raise ValueError('Complete retry capture plan required')
        retry=json.loads(read_artifact(retry_audits[0]))
        targets=discover_profiles(read_artifact(indices[0]),start_year=retry['requested_start_year'],end_year=retry['requested_end_year'])
        if targets!=retry['targets']:raise ValueError('Retry directory plan mismatch')
        verified=[]
        for page in [a for a in records if a.metadata_.get('endpoint')=='annual_page']:
            target=next((t for t in targets if t['page_url']==page.source_url),None)
            if target is None:raise ValueError('Unlinked retry annual page')
            verified.extend(discover_downloads(read_artifact(page),reference=target))
        if verified!=retry['references']:raise ValueError('Retry official download plan mismatch')
        for item in retry['captured']:
            reference=item['reference']
            if reference not in collection['references'] or reference not in verified:
                raise ValueError('Retry report is not in original official source plan')
            if any(old['reference']['year']==reference['year'] for old in collection['captured']):
                raise ValueError('Retry may only fill a missing annual profile')
            reports=[a for a in records if a.metadata_.get('endpoint')=='profile_pdf' and a.source_url==item['request_url'] and a.sha256==item['sha256']]
            if len(reports)!=1:raise ValueError('Retry PDF manifest mismatch')
            read_artifact(reports[0]);active.extend(reports)
            for link in item['resolution']:
                chain=[a for a in records if a.metadata_.get('endpoint')==link['endpoint'] and a.source_url==link['url'] and a.sha256==link['sha256']]
                if len(chain)!=1:raise ValueError('Retry resolution evidence mismatch')
                read_artifact(chain[0]);active.extend(chain)
            collection['captured'].append(item)
            for error in collection['errors']:
                if error.get('year')==reference['year']:
                    error['resolved_by_retry']={'manifest':str(Path(retry_path).resolve().relative_to(ROOT)),'pdf_sha256':item['sha256']}
        collection.setdefault('retry_manifests',[]).append({'path':str(Path(retry_path).resolve().relative_to(ROOT)),
            'sha256':hashlib.sha256(Path(retry_path).read_bytes()).hexdigest()})
    collection['primary_manifest']={'path':str(Path(manifest_path).resolve().relative_to(ROOT)),
        'sha256':hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()}
    writer=FileSnapshotWriter(Path(directory).resolve());writer.artifacts=active
    asyncio.run(writer.write(json.dumps(collection,ensure_ascii=False,sort_keys=True).encode(),
        source_url=audits[0].source_url,kind='archive_json',metadata={'endpoint':'collection_audit'}))
    return writer.directory/'manifest.json',writer.artifacts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-year',type=int,default=HISTORY_START_YEAR)
    parser.add_argument('--end-year',type=int)
    parser.add_argument('--raw-directory',type=Path)
    parser.add_argument('--replay-manifest',type=Path)
    parser.add_argument('--retry-manifest',type=Path,action='append',default=[],help='Explicit live retries to fill missing captured years in --replay-manifest')
    parser.add_argument('--capture-only',action='store_true')
    args=parser.parse_args()
    output=ROOT/'data/current/id'; output.mkdir(parents=True,exist_ok=True)
    if args.replay_manifest:
        manifest_path=args.replay_manifest.resolve()
        artifacts=[SimpleNamespace(**a) for a in json.loads(manifest_path.read_text())['artifacts']]
        if args.retry_manifest:
            if not args.raw_directory:parser.error('--retry-manifest requires a new --raw-directory')
            manifest_path,artifacts=merge_retry_captures(manifest_path,args.retry_manifest,args.raw_directory)
    else:
        if args.retry_manifest:parser.error('--retry-manifest requires --replay-manifest')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        writer=FileSnapshotWriter((args.raw_directory or ROOT/'data/raw/id/kemkes-history'/stamp).resolve())
        asyncio.run(collect(writer,start_year=args.start_year,end_year=args.end_year))
        manifest_path=writer.directory/'manifest.json'; artifacts=writer.artifacts
    print(f'Captured {len(artifacts)} artifacts; manifest {manifest_path}',flush=True)
    if args.capture_only: return
    result=organize(artifacts,updater=IndonesiaKemkesProfileUpdater(),start_year=args.start_year,
        end_year=args.end_year,parse_cache_directory=ROOT/'data/cache/id/kemkes-parses')
    temporary=result.source_csv.with_suffix('.csv.tmp')
    with temporary.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(result.rows[0]));writer.writeheader();writer.writerows(result.rows)
    temporary.replace(result.source_csv)
    result.source_audit['raw_manifest']=str(manifest_path.relative_to(ROOT))
    result.source_audit['database_registered']=False
    (output/'source_audit.json').write_text(json.dumps(result.source_audit,ensure_ascii=False,indent=2))
    print('\n'.join(result.script_logs),flush=True)
    print(f'CSV {result.source_csv}; database import pending',flush=True)

if __name__=='__main__':main()
