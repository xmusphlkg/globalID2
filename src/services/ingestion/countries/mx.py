"""Durably capture linked DGE annual PDFs; replay using only verified artifacts."""
import asyncio
from datetime import datetime,timezone
import json
from src.data.crawlers.mx import DEFAULT_SCOPE,HISTORY_START_YEAR,NATIONAL_URL,INCIDENCE_URL,MexicoDGEAnnualCrawler,MexicoDGEContractError,parse_national_pdf,discover_national_tables
from src.data.processors.mx import MexicoDGEAnnualUpdater
from src.services.ingestion.artifacts import read_artifact

async def collect(writer,*,source=DEFAULT_SCOPE,start_year=HISTORY_START_YEAR,end_year=None,force=False,cache_ttl_days=1,**options):
    MexicoDGEAnnualUpdater.normalize_source(source);crawler=MexicoDGEAnnualCrawler()
    try:
        refs,indexes=await asyncio.to_thread(crawler.discover,start_year=start_year,end_year=end_year)
        for url,payload in indexes:await writer.write(payload,source_url=url,kind='mx_dge_index',metadata={'role':'national_discovery'})
        captured=[];errors=[]
        for ref in refs:
            try:
                payload,cache_metadata=await asyncio.to_thread(crawler.download,ref,force=force,cache_ttl_days=cache_ttl_days)
                if payload[:4]!=b'%PDF':raise MexicoDGEContractError('Source returned non-PDF bytes')
            except Exception as exc:
                errors.append({'url':ref['url'],'stage':'download','error':str(exc)});continue
            # Persistence failure must propagate rather than masquerade as a
            # source download gap after a successful response.
            artifact=await writer.write(payload,source_url=ref['url'],kind='mx_dge_pdf',metadata={**ref,**cache_metadata})
            captured.append({'url':ref['url'],'sha256':artifact.sha256})
        plan={'references':refs,'captured':captured,'errors':errors,'requested_start_year':start_year,'requested_end_year':end_year}
        await writer.write(json.dumps(plan,ensure_ascii=False).encode(),source_url=indexes[0][0],kind='mx_dge_plan',metadata={'role':'collection_plan'})
        return writer.artifacts
    finally:crawler.session.close()

def organize(artifacts,*,updater,source=DEFAULT_SCOPE,**options):
    updater.normalize_source(source)
    plans=[a for a in artifacts if a.kind=='mx_dge_plan']
    if len(plans)!=1:raise MexicoDGEContractError('Exactly one frozen DGE collection plan is required')
    plan=json.loads(read_artifact(plans[0]));pdfs=[a for a in artifacts if a.kind=='mx_dge_pdf']
    indexes={a.source_url:a for a in artifacts if a.kind=='mx_dge_index'}
    if set(indexes)!={NATIONAL_URL,INCIDENCE_URL}:raise MexicoDGEContractError('Both frozen official DGE discovery menus are required')
    discovered=discover_national_tables(read_artifact(indexes[NATIONAL_URL]),incidence_payload=read_artifact(indexes[INCIDENCE_URL]),start_year=plan.get('requested_start_year') or HISTORY_START_YEAR,end_year=plan.get('requested_end_year'))
    if discovered!=plan['references']:raise MexicoDGEContractError('DGE collection references differ from captured official menus')
    expected={item['url']:item['sha256'] for item in plan['captured']}
    if len(expected)!=len(plan['captured']) or {a.source_url:a.sha256 for a in pdfs}!=expected or len(pdfs)!=len(expected):raise MexicoDGEContractError('DGE captured artifact manifest differs from frozen plan')
    references={r['url']:r for r in plan['references']};rows=[];failures=[];reports=[]
    for artifact in pdfs:
        payload=read_artifact(artifact)
        if artifact.source_url not in references:raise MexicoDGEContractError('DGE artifact not present in discovery plan')
        try:
            parsed,report=parse_national_pdf(payload,reference=references[artifact.source_url],retrieved_at=artifact.metadata_.get('retrieved_at'))
        except MexicoDGEContractError as exc:
            failures.append(f'{artifact.source_url}: {exc}');continue
        rows.extend(parsed)
        reports.append(report)
    if not rows:raise MexicoDGEContractError('No supported DGE source tables could be organized')
    failed_count=len(failures)+len(plan['errors'])+sum(r.get('failed_count',0) for r in reports)
    audit={'status':'partial' if failed_count else 'parsed','discovered_tables':len(references),'archived_tables':len(pdfs),'parsed_tables':len(reports),'failed_count':failed_count,'failed_tables':failures,'download_failures':plan['errors'],'reports':reports,'rows':len(rows),'numeric_rows':sum(bool(r['Cases']) for r in rows),'missing_rows':sum(not r['Cases'] for r in rows),'coverage_complete':not failed_count,'channel_url':NATIONAL_URL,'archive_url':INCIDENCE_URL,'requested_start_year':plan.get('requested_start_year'),'requested_end_year':plan.get('requested_end_year')}
    result=updater.prepare_rows(rows,source=source,source_audit=audit,**options)
    result.script_logs.extend([f'[coverage] Archived {len(pdfs)}/{len(references)} PDFs; parsing failures {len(failures)}; download failures {len(plan["errors"])}']+[f'[parse gap] {failure}' for failure in failures])
    return result
