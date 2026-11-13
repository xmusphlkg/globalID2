"""Validate a private template-v2 candidate used only as v3 migration input.

Uploading or activating paper-template-v2 is no longer a publication path.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

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
    """Reject the retired template-v2 uploader before it touches remote state."""
    del directory, client, workers, activate, max_total_bytes
    raise ValueError('Paper template v2 publication has been removed')
