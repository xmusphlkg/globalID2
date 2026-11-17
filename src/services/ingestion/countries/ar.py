"""Archive all BEN publications before offline conservative ENO organization."""
from __future__ import annotations
import asyncio
import json
from src.data.crawlers.ar import ArgentinaBENCrawler, DEFAULT_SCOPE, HISTORY_START_YEAR, parse_ben_payload
from src.data.processors.ar import ArgentinaBENUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, force=False, **options):
    ArgentinaBENUpdater.normalize_source(source)
    crawler = ArgentinaBENCrawler()
    try:
        refs, indexes, failures = await asyncio.to_thread(crawler.discover, start_year=start_year or HISTORY_START_YEAR)
        for url, payload in indexes:
            await writer.write(payload, source_url=url, kind="ar_ben_index", metadata={"role": "archive_discovery"})
        success = 0
        for reference in refs:
            try:
                payload, meta = await asyncio.to_thread(crawler.download, reference, force=force)
            except Exception as exc:
                failures.append({"url": reference["url"], "stage": "download", "error": str(exc)})
                continue
            # Persistence failures propagate, never masquerade as an upstream gap.
            await writer.write(payload, source_url=reference["url"], kind="ar_ben_pdf", metadata=meta)
            success += 1
        plan = {"references": refs, "failures": failures, "downloaded_reports": success, "requested_start_year": start_year or HISTORY_START_YEAR}
        await writer.write(json.dumps(plan, ensure_ascii=False).encode(), source_url="https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional", kind="ar_ben_plan", metadata={"role": "collection_plan"})
        if not success:
            raise ValueError("No BEN publications captured")
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    plans = [a for a in artifacts if a.kind == "ar_ben_plan"]
    if len(plans) != 1:
        raise ValueError("Exactly one BEN collection plan required")
    plan = json.loads(read_artifact(plans[0]))
    failures = list(plan.get("failures", []));audits=[];rows=[]
    for artifact in artifacts:
        if artifact.kind != "ar_ben_pdf":
            continue
        payload = read_artifact(artifact)  # hash/persistence errors must propagate
        try:
            parsed, audit = parse_ben_payload(payload, source_url=artifact.source_url, reference=artifact.metadata_.get("reference"))
            rows.extend(parsed);audits.append(audit)
        except Exception as exc:
            failures.append({"url": artifact.source_url, "stage": "parse", "error": str(exc)})
    audit = {"discovered_reports": len(plan["references"]), "reports": audits, "failures": failures, "status": "partial" if failures or any(not a["parsed_rows"] for a in audits) else "validated_supported_tables"}
    return updater.prepare_rows(rows, source=source, source_audit=audit, **options)
