"""Integrity-checked NICD report cache with bounded source revalidation."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path

class NICDReportCache:
 def __init__(self,directory=Path('data/raw/za/nicd_nmc_transport_cache')):
  self.directory=Path(directory)
 def get(self,crawler,url,*,ttl_hours=24,force_refresh=False,now=None):
  now=now or datetime.now(timezone.utc);self.directory.mkdir(parents=True,exist_ok=True)
  receipt_path=self.directory/(hashlib.sha256(url.encode()).hexdigest()+'.json');cached=None;payload=None
  if receipt_path.exists():
   cached=json.loads(receipt_path.read_text());path=Path(cached['path'])
   if not path.resolve().is_relative_to(self.directory.resolve()):raise ValueError('NICD cache receipt path escapes cache')
   payload=path.read_bytes()
   if len(payload)!=cached['byte_size'] or hashlib.sha256(payload).hexdigest()!=cached['sha256']:raise ValueError('NICD cache content integrity failed')
   checked=datetime.fromisoformat(cached.get('revalidated_at') or cached['retrieved_at'])
   if not force_refresh and now-checked<timedelta(hours=max(0,float(ttl_hours))):return payload,cached|{'cache_status':'hit_verified'}
  headers={}
  if cached:
   if cached.get('etag'):headers['If-None-Match']=cached['etag']
   if cached.get('last_modified'):headers['If-Modified-Since']=cached['last_modified']
  response=crawler.get(url,headers=headers)
  if response.status_code==304:
   if cached is None:raise ValueError('NICD returned 304 without cached original')
   receipt=cached|{'revalidated_at':now.isoformat()};status='revalidated_not_modified'
  else:
   payload=response.content
   if not(payload.startswith(b'%PDF') or payload.startswith(b'PK')):raise ValueError('NICD report cache refuses non-document response')
   sha=hashlib.sha256(payload).hexdigest();suffix='.pdf' if payload.startswith(b'%PDF') else '.docx';path=self.directory/(sha+suffix)
   if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('NICD immutable cache object integrity failed')
   path.write_bytes(payload)
   receipt={'url':url,'path':str(path),'sha256':sha,'byte_size':len(payload),'retrieved_at':now.isoformat(),'revalidated_at':now.isoformat(),'etag':response.headers.get('ETag'),'last_modified':response.headers.get('Last-Modified')};status='refetched' if cached else 'downloaded'
  tmp=receipt_path.with_suffix('.tmp');tmp.write_text(json.dumps(receipt,indent=2));tmp.replace(receipt_path)
  return payload,receipt|{'cache_status':status}
