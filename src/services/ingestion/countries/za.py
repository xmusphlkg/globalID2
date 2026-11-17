"""Live NICD catalogue discovery, durable originals and pure offline parsing."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
from datetime import datetime,timezone
from pathlib import Path
from src.data.crawlers.za import DEFAULT_SCOPE,INDEX_URL,MEDIA_URLS,SouthAfricaNICDNMCCrawler,discover_reports,discover_media,parse_report
from src.services.ingestion.artifacts import read_artifact

async def collect(writer,*,source=DEFAULT_SCOPE,start_year=None,cache_ttl_hours=24,force_refresh=False,**options):
 from src.data.processors.za import SouthAfricaNICDNMCUpdater
 SouthAfricaNICDNMCUpdater.normalize_source(source)
 crawler=SouthAfricaNICDNMCCrawler();failures=[];catalogues=[]
 try:
  response=await asyncio.to_thread(crawler.get,INDEX_URL)
  await writer.write(response.content,source_url=response.url,kind='za_nicd_index',metadata={'retrieved_at':datetime.now(timezone.utc).isoformat()})
  reports=discover_reports(response.content)
  catalogues.append({'url':INDEX_URL,'status':'captured'})
  for base in MEDIA_URLS:
   page=1;pages=1
   while page<=pages:
    url=base if page==1 else base+f'&page={page}'
    try:
     response=await asyncio.to_thread(crawler.get,url);found=discover_media(response.content,reports)
     pages=int(response.headers.get('X-WP-TotalPages','1'))
    except Exception as exc:failures.append({'url':url,'kind':'media_catalogue','error':str(exc)});break
    await writer.write(response.content,source_url=response.url,kind='za_nicd_media_index',metadata={'page':page,'total_pages':pages})
    reports=found;catalogues.append({'url':url,'status':'captured'});page+=1
  discovered_count=len(reports)
  def included(report):
   years=[int(v) for v in re.findall(r'(?<!\d)(20\d{2})(?!\d)',report.get('title',''))]
   return start_year is None or not years or max(years)>=int(start_year)
  reports=[r for r in reports if included(r)]
  from src.data.crawlers.za_cache import NICDReportCache
  cache=NICDReportCache()
  for report in reports:
   try:
    payload,cache_receipt=await asyncio.to_thread(cache.get,crawler,report['url'],ttl_hours=cache_ttl_hours,force_refresh=force_refresh or bool(options.get('force')))
   except Exception as exc:failures.append(report|{'error':str(exc)});continue
   await writer.write(payload,source_url=report['url'],kind='za_nicd_report',metadata={'report':report,'retrieved_at':cache_receipt['retrieved_at'],'revalidated_at':cache_receipt['revalidated_at'],'cache_status':cache_receipt['cache_status'],'content_sha256':hashlib.sha256(payload).hexdigest()})
  plan={'reports':reports,'failed_downloads':failures,'catalogue_audit':catalogues,'mode':'live_discovery_with_verified_report_cache','requested_start_year':start_year,'catalogue_discovered_reports':discovered_count,'cache_ttl_hours':cache_ttl_hours,'channel_url':INDEX_URL,'archive_url':INDEX_URL,'discovery_endpoints':[INDEX_URL,*MEDIA_URLS]}
  await writer.write(json.dumps(plan).encode(),source_url=INDEX_URL,kind='za_nicd_plan')
  return writer.artifacts
 finally:crawler.session.close()

def organize_documents(documents,*,expected_reports=None,failed_downloads=None):
 rows=[];audit={'status':'partial','reports':[],'failed_downloads':list(failed_downloads or []),'expected_reports':len(expected_reports or []),'channel_url':INDEX_URL,'archive_url':INDEX_URL,'discovery_endpoints':[INDEX_URL,*MEDIA_URLS]}
 for report,payload in documents:
  try:parsed,detail=parse_report(payload,report)
  except Exception as exc:audit['reports'].append({'url':report['url'],'status':'unparsed','error':str(exc)});continue
  rows.extend(parsed);audit['reports'].append({'url':report['url'],'status':'parsed','observations':len(parsed),**detail})
 selected={};conflicts={};superseded=0
 for row in rows:
  identity=(row['Date'],row['SourceDiseaseCode'],row['GeographyKey'],row['DefinitionVersion'])
  # Current archive links identify the preferred edition within a report
  # period. Publication metadata settles editions only after that priority;
  # retrieval time never authorizes revising a historical number.
  rank=(int(row['SourceReportYear']),int(row['SourceReportMonth']),row['SourceReportCurrentLink']=='true',row.get('SourceReportPublicationDate',''))
  old=selected.get(identity)
  if old is None or rank>old[0]:
   if old:superseded+=1
   selected[identity]=(rank,row);conflicts.pop(identity,None)
  elif rank==old[0] and row['Cases']!=old[1]['Cases']:conflicts[identity]=[old[1]['SourceURL'],row['SourceURL']]
  else:superseded+=1
 rows=[value[1] for key,value in selected.items() if key not in conflicts];rows.sort(key=lambda r:(r['Date'],r['SourceDiseaseCode'],r['GeographyKey']))
 audit.update(captured_reports=len(audit['reports']),parsed_reports=sum(r['status']=='parsed' for r in audit['reports']),unparsed_reports=sum(r['status']=='unparsed' for r in audit['reports']),observations=len(rows),superseded_observations=superseded,conflicting_observations=[{'key':list(k),'urls':v} for k,v in conflicts.items()],coverage_note='Complete discovered official report originals; verified native disease tables only. Missing publication months, failed tables, back-capture, percentages and graphs are not zeroes. Provincial, national, quarter, comparison and classification cohorts remain distinct.')
 return rows,audit

def organize(artifacts,*,updater,source=DEFAULT_SCOPE,**options):
 updater.normalize_source(source);plans=[a for a in artifacts if a.kind=='za_nicd_plan']
 if len(plans)!=1:raise ValueError('Exactly one ZA NICD capture plan required')
 plan=json.loads(read_artifact(plans[0]));documents=((a.metadata_['report'],read_artifact(a)) for a in artifacts if a.kind=='za_nicd_report')
 rows,audit=organize_documents(documents,expected_reports=plan['reports'],failed_downloads=plan.get('failed_downloads'))
 audit.update(collection_mode=plan.get('mode'),catalogue_audit=plan.get('catalogue_audit',[]),requested_start_year=plan.get('requested_start_year'),catalogue_discovered_reports=plan.get('catalogue_discovered_reports',len(plan['reports'])),cache_ttl_hours=plan.get('cache_ttl_hours'))
 return updater.prepare_rows(rows,source=source,source_audit=audit,**options)
