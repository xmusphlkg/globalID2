"""Publish template candidate objects before an optional atomic pointer switch.

The old static Papers objects are never touched. This is an explicit migration
entry point, not yet part of the existing site-release task.
"""
from __future__ import annotations
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from .papers_r2 import R2S3Error

PREFIX='paper-template-v2'
_HASH=re.compile(r'[a-f0-9]{64}\Z')


def _load(root: Path, group: str, digest: str) -> tuple[bytes,dict]:
    if not isinstance(digest,str) or not _HASH.fullmatch(digest): raise ValueError('Invalid object digest')
    path=root/group/f'{digest}.json'
    if path.is_symlink() or not path.is_file(): raise ValueError('Missing or unsafe candidate object')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('Candidate checksum mismatch')
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('schema')!=1: raise ValueError('Invalid candidate object')
    return raw,value


def validate_candidate(directory: Path) -> tuple[bytes,dict[str,Path],dict]:
    root=Path(directory).resolve()
    pointer=(root/'current.json').read_bytes()
    current=json.loads(pointer)
    if not isinstance(current,dict) or current.get('schema')!=1: raise ValueError('Invalid current pointer')
    generation=current.get('generation')
    _,manifest=_load(root,'generations',generation)
    files={f'generations/{generation}.json':root/'generations'/f'{generation}.json'}
    locales=manifest.get('locales')
    if not isinstance(locales,dict) or set(locales)!={'en','zh','fr'}: raise ValueError('Invalid locale manifest')
    for spec in locales.values():
        if not isinstance(spec,dict): raise ValueError('Invalid locale')
        template=spec.get('template');_load(root,'templates',template)
        files[f'templates/{template}.json']=root/'templates'/f'{template}.json'
    indexes=manifest.get('indexes')
    if not isinstance(indexes,dict): raise ValueError('Invalid indexes')
    count=0
    for shard,index_hash in indexes.items():
        if not isinstance(shard,str) or not re.fullmatch(r'[a-f0-9]{2}',shard): raise ValueError('Invalid shard')
        _,index=_load(root,'indexes',index_hash)
        if not isinstance(index.get('items'),dict): raise ValueError('Invalid index')
        files[f'indexes/{index_hash}.json']=root/'indexes'/f'{index_hash}.json'
        for slug,record_hash in index['items'].items():
            if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*',slug) or hashlib.sha256(slug.encode()).hexdigest()[:2]!=shard:
                raise ValueError('Invalid paper identity')
            if not isinstance(record_hash,str) or not _HASH.fullmatch(record_hash): raise ValueError('Invalid record digest')
            path=root/'records'/f'{record_hash}.json'
            if path.is_symlink() or not path.is_file(): raise ValueError('Missing or unsafe paper record')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=record_hash: raise ValueError('Candidate checksum mismatch')
            record=json.loads(raw)
            if record.get('schema')!=2 or record.get('slug')!=slug or not isinstance(record.get('locales'),dict) or set(record['locales'])!={'en','zh','fr'}: raise ValueError('Invalid paper record')
            for item in record['locales'].values():
                if not isinstance(item,dict) or not isinstance(item.get('main'),str) or not isinstance(item.get('head'),list): raise ValueError('Invalid localized record')
            files[f'records/{record_hash}.json']=root/'records'/f'{record_hash}.json'
            count+=1
    if not count: raise ValueError('Empty paper index')
    counts={locale:count for locale in locales}
    return pointer,files,counts


def publish_candidate(directory: Path, client, *, workers: int=32, activate: bool=False, max_total_bytes: int=8_000_000_000) -> dict:
    if type(workers) is not int or not 1<=workers<=96: raise ValueError('Workers must be 1–96')
    if type(max_total_bytes) is not int or max_total_bytes<=0: raise ValueError('Invalid storage budget')
    pointer,files,counts=validate_candidate(directory)
    pointer_key=f'{PREFIX}/current.json'
    previous=client.get_object(pointer_key) if activate else None
    inventory=client.list_objects()
    # Do not remove old objects or assume they can be reclaimed yet.
    bound=sum(int(x['bytes']) for x in inventory.values())+sum(p.stat().st_size for k,p in files.items() if f'{PREFIX}/{k}' not in inventory)+len(pointer)
    if bound>max_total_bytes: raise R2S3Error(f'Storage budget exceeded before upload: {bound} > {max_total_bytes}')
    def upload(pair):
        relative,path=pair;raw=path.read_bytes();expected=Path(relative).stem
        if hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('Candidate changed during upload')
        encoded=gzip.compress(raw,compresslevel=6,mtime=0) if len(raw)>1024 else raw
        compressed=len(encoded)<len(raw);wire=encoded if compressed else raw
        key=f'{PREFIX}/{relative}';stored=inventory.get(key) or {}
        if stored.get('bytes')==len(wire) and stored.get('etag')==hashlib.md5(wire,usedforsecurity=False).hexdigest(): return False
        try:
            client.put_object_conditional(key,wire,previous=None,content_type='application/json',cache_control='public, max-age=31536000, immutable',content_encoding='gzip' if compressed else None)
        except R2S3Error as error:
            if error.status!=412: raise
            existing=client.get_object(key)
            if existing and existing.startswith(b'\x1f\x8b'): existing=gzip.decompress(existing)
            if existing!=raw: raise R2S3Error(f'Immutable object conflict: {relative}') from error
            return False
        return True
    uploaded = processed = 0
    started = last_report = time.monotonic()
    print(f'Template staging: {len(files)} immutable objects; storage upper bound {bound} bytes', flush=True)
    # Bound queued work and report completions rather than submission order:
    # one slow object must not hide progress by blocking Executor.map().
    entries = iter(sorted(files.items()))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        def fill():
            while len(pending) < workers * 2:
                item = next(entries, None)
                if item is None: break
                pending.add(pool.submit(upload, item))
        fill()
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    uploaded += int(future.result())
                    processed += 1
                fill()
                now = time.monotonic()
                if now - last_report >= 15:
                    print(f'Template staging: {processed}/{len(files)}; uploaded={uploaded}; reused={processed-uploaded}; seconds={now-started:.1f}', flush=True)
                    last_report = now
        except BaseException:
            for future in pending: future.cancel()
            raise
    if activate:
        try: client.put_object_conditional(pointer_key,pointer,previous=previous,cache_control='no-store')
        except R2S3Error as error:
            if error.status!=412 or client.get_object(pointer_key)!=pointer: raise
    return {'generation':json.loads(pointer)['generation'],'pages':counts,'objects':len(files),'uploaded':uploaded,'reused':len(files)-uploaded,'activated':activate,'storage_upper_bound':bound}
