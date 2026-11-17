"""Capture official DGE high-frequency originals before offline organization."""
from __future__ import annotations

import asyncio
import json

from src.data.crawlers.mx_high_frequency import (
    MONTHLY_SCOPE, NATIONAL_URL, MexicoDGEHighFrequencyCrawler,
    MexicoDGEContractError, discover_monthly_tables, discover_weekly_links,
    normalize_scope, parse_monthly_pdf, parse_weekly_pdf,
)
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=MONTHLY_SCOPE, start_year=None, end_year=None,
                  force=False, cache_ttl_days=1, latest_reports=None,
                  weekly_index_url=None, **options):
    scope=normalize_scope(source)
    crawler=MexicoDGEHighFrequencyCrawler(source=scope)
    try:
        refs,indexes=await asyncio.to_thread(crawler.discover,start_year=start_year,
            end_year=end_year,latest_reports=latest_reports,weekly_index_url=weekly_index_url)
        for url,payload in indexes:
            await writer.write(payload,source_url=url,kind="mx_hf_index",metadata={"scope":scope,"role":"official_discovery"})
        captured=[];failures=[];ocr_captured=[]
        for ref in refs:
            try:
                payload,meta=await asyncio.to_thread(crawler.download,ref,force=force,cache_ttl_days=cache_ttl_days)
            except Exception as exc:
                failures.append({"url":ref["url"],"stage":"download","error":str(exc)});continue
            artifact=await writer.write(payload,source_url=ref["url"],kind="mx_hf_pdf",metadata={**ref,**meta,"scope":scope})
            captured.append({"url":ref["url"],"sha256":artifact.sha256})
            if scope!=MONTHLY_SCOPE:
                from src.data.crawlers.mx_title_ocr import capture_summary_captions
                evidence=await asyncio.to_thread(capture_summary_captions,payload)
                if evidence:
                    ocr_artifact=await writer.write(json.dumps(evidence,ensure_ascii=False).encode(),source_url=ref['url'],kind='mx_hf_title_ocr',metadata={'scope':scope,'pdf_sha256':artifact.sha256})
                    ocr_captured.append({'url':ref['url'],'sha256':ocr_artifact.sha256})
        plan={"scope":scope,"references":refs,"captured":captured,"failures":failures,
              "requested_start_year":start_year,"requested_end_year":end_year,
              "latest_reports":latest_reports,"index_urls":[url for url,_ in indexes],"title_ocr_captured":ocr_captured}
        await writer.write(json.dumps(plan,ensure_ascii=False).encode(),source_url=indexes[0][0],kind="mx_hf_plan",metadata={"scope":scope,"role":"collection_plan"})
        if not captured:raise MexicoDGEContractError("No official DGE high-frequency originals captured")
        return writer.artifacts
    finally:crawler.close()


def organize(artifacts, *, updater, source=MONTHLY_SCOPE, **options):
    scope=normalize_scope(source)
    plans=[a for a in artifacts if a.kind=="mx_hf_plan"]
    if len(plans)!=1:raise MexicoDGEContractError("Exactly one DGE high-frequency frozen plan required")
    plan=json.loads(read_artifact(plans[0]))
    if plan["scope"]!=scope or updater.source_scope!=scope:raise MexicoDGEContractError("DGE source scope mismatch")
    index_list=[a for a in artifacts if a.kind=="mx_hf_index"]
    indexes={a.source_url:read_artifact(a) for a in index_list}
    if len(indexes)!=len(index_list) or set(indexes)!=set(plan["index_urls"]):raise MexicoDGEContractError("DGE frozen discovery index set differs")
    if scope==MONTHLY_SCOPE:
        if set(indexes)!={NATIONAL_URL}:raise MexicoDGEContractError("Official DGE monthly national menu required")
        discovered=discover_monthly_tables(indexes[NATIONAL_URL],start_year=plan["requested_start_year"] or 1984,end_year=plan["requested_end_year"])
    else:
        discovered=[]
        for url,payload in indexes.items():
            discovered.extend(discover_weekly_links(payload,page_url=url,start_year=plan["requested_start_year"],end_year=plan["requested_end_year"]))
        discovered=sorted({r["url"]:r for r in discovered}.values(),key=lambda r:(r["year"],r["issue_week"]))
        if plan["latest_reports"] is not None:discovered=discovered[-max(0,int(plan["latest_reports"])):]
    if discovered!=plan["references"]:raise MexicoDGEContractError("DGE plan references differ from captured official indexes")
    pdfs=[a for a in artifacts if a.kind=="mx_hf_pdf"]
    expected={r["url"]:r["sha256"] for r in plan["captured"]}
    if len(expected)!=len(plan["captured"]) or len(pdfs)!=len(expected) or {a.source_url:a.sha256 for a in pdfs}!=expected:raise MexicoDGEContractError("DGE PDF artifact manifest differs from frozen capture plan")
    refs={r["url"]:r for r in plan["references"]};rows=[];reports=[];failures=list(plan["failures"])
    ocr_artifacts=[a for a in artifacts if a.kind=='mx_hf_title_ocr']
    expected_ocr={item['url']:item['sha256'] for item in plan.get('title_ocr_captured',[])}
    if len(expected_ocr)!=len(plan.get('title_ocr_captured',[])) or {a.source_url:a.sha256 for a in ocr_artifacts}!=expected_ocr:raise MexicoDGEContractError('DGE OCR artifact manifest differs from frozen capture plan')
    ocr_evidence={a.source_url:json.loads(read_artifact(a)) for a in ocr_artifacts}
    if len(ocr_evidence)!=len(ocr_artifacts) or not set(ocr_evidence)<=set(expected):raise MexicoDGEContractError('DGE OCR artifact set does not match captured originals')
    parser=parse_monthly_pdf if scope==MONTHLY_SCOPE else parse_weekly_pdf
    for artifact in pdfs:
        payload=read_artifact(artifact)  # Integrity failures are fatal, not coverage gaps.
        if artifact.source_url not in refs:raise MexicoDGEContractError("DGE original absent from frozen references")
        try:
            kwargs={'title_ocr':ocr_evidence[artifact.source_url]} if artifact.source_url in ocr_evidence and scope!=MONTHLY_SCOPE else {}
            parsed,audit=parser(payload,reference=refs[artifact.source_url],retrieved_at=artifact.metadata_.get("retrieved_at"),**kwargs)
        except MexicoDGEContractError as exc:
            failures.append({"url":artifact.source_url,"stage":"parse","error":str(exc)});continue
        rows.extend(parsed);reports.append(audit)
    failed_count=len(failures)+sum(r["failed_count"] for r in reports)
    audit={"scope":scope,"status":"partial" if failed_count else "parsed",
           "discovered_tables":len(refs),"archived_tables":len(pdfs),"parsed_tables":len(reports),
           "rows":len(rows),"numeric_rows":sum(bool(r["Cases"]) for r in rows),
           "reports":reports,"failures":failures,"failed_count":failed_count,
           "coverage_complete":not failed_count and len(pdfs)==len(refs),
           "requested_start_year":plan["requested_start_year"],"requested_end_year":plan["requested_end_year"]}
    return updater.prepare_rows(rows,source=scope,source_audit=audit,**options)
