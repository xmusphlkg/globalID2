"""Discover current official national month tables; verify and replay offline."""
import asyncio
from datetime import datetime, timezone
import json
from src.data.crawlers.eu_monthly import CONTRACTS, EUMonthlyCrawler, discover_indexes, discover_reports, parse_pdf
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, country_code, source=None, start_year=None, end_year=None, **options):
    code = country_code.upper(); contract = CONTRACTS[code]
    if source not in {None, contract["scope"]}:
        raise ValueError("Unexpected national monthly source scope")
    start = int(start_year or contract["start_year"]); end = int(end_year) if end_year is not None else None
    crawler = EUMonthlyCrawler(); retrieved = datetime.now(timezone.utc).isoformat()
    indexes, references, excluded, errors = [], [], [], []
    try:
        response = await asyncio.to_thread(crawler.get, contract["url"])
        await writer.write(response.content, source_url=contract["url"], kind="eu_monthly_index", metadata={"country_code": code, "role": "root", "retrieved_at": retrieved})
        indexes.append((contract["url"], response.content, None))
        children = discover_indexes(response.content, country_code=code, url=contract["url"], start_year=start, end_year=end)
        for child in children:
            response = await asyncio.to_thread(crawler.get, child["url"])
            await writer.write(response.content, source_url=child["url"], kind="eu_monthly_index", metadata={"country_code": code, "role": "year", "edition_year": child["edition_year"], "retrieved_at": retrieved})
            indexes.append((child["url"], response.content, child["edition_year"]))
        for url, payload, year in indexes:
            if code == "CZ" and year is None:
                continue
            found, ignored = discover_reports(payload, country_code=code, url=url, start_year=start, end_year=end, edition_year=year)
            references.extend(found); excluded.extend(ignored)
        refs = {r["url"]: r for r in references}
        if len(refs) != len(references) or not refs:
            raise ValueError("Monthly discovery contains duplicate reports or no supported PDFs")
        captured = []
        for ref in references:
            try:
                response = await asyncio.to_thread(crawler.get, ref["url"])
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Monthly report returned non-PDF bytes")
            except Exception as exc:
                errors.append({"url": ref["url"], "error": str(exc)}); continue
            artifact = await writer.write(response.content, source_url=ref["url"], kind="eu_monthly_pdf", metadata={**ref, "retrieved_at": retrieved})
            captured.append({"url": ref["url"], "sha256": artifact.sha256})
        plan = {"country_code": code, "start_year": start, "end_year": end, "references": references, "captured": captured, "excluded": excluded, "errors": errors, "retrieved_at": retrieved}
        await writer.write(json.dumps(plan, ensure_ascii=False).encode(), source_url=contract["url"], kind="eu_monthly_plan", metadata={"retrieved_at": retrieved})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=None, **options):
    code = updater.country_code; contract = CONTRACTS[code]; updater.normalize_source(source or contract["scope"])
    plans = [a for a in artifacts if a.kind == "eu_monthly_plan"]
    if len(plans) != 1:
        raise ValueError("Exactly one frozen monthly discovery plan is required")
    plan = json.loads(read_artifact(plans[0]))
    if plan["country_code"] != code:
        raise ValueError("Monthly plan country contradicts updater")
    indexes = [a for a in artifacts if a.kind == "eu_monthly_index"]
    byurl = {a.source_url: a for a in indexes}
    if len(indexes) != len(byurl) or contract["url"] not in byurl:
        raise ValueError("Monthly discovery indexes are missing or duplicate")
    root = byurl[contract["url"]]
    children = discover_indexes(read_artifact(root), country_code=code, url=contract["url"], start_year=plan["start_year"], end_year=plan["end_year"])
    expected_indexes = {contract["url"]} | {x["url"] for x in children}
    if set(byurl) != expected_indexes:
        raise ValueError("Monthly captured year indexes differ from official root")
    discovered, exclusions = [], []
    for ref in (children if code == "CZ" else [{"url": contract["url"], "edition_year": None}]):
        found, ignored = discover_reports(read_artifact(byurl[ref["url"]]), country_code=code, url=ref["url"], start_year=plan["start_year"], end_year=plan["end_year"], edition_year=ref["edition_year"])
        discovered.extend(found); exclusions.extend(ignored)
    if discovered != plan["references"]:
        raise ValueError("Monthly report plan differs from frozen official indexes")
    pdfs = [a for a in artifacts if a.kind == "eu_monthly_pdf"]
    expected = {r["url"]: r["sha256"] for r in plan["captured"]}
    if len(pdfs) != len(expected) or {a.source_url: a.sha256 for a in pdfs} != expected:
        raise ValueError("Monthly PDF artifact manifest differs from collection plan")
    refs = {r["url"]: r for r in discovered}; rows, reports, failures = [], [], []
    as_of = datetime.fromisoformat(plan["retrieved_at"].replace("Z", "+00:00")).date()
    for artifact in pdfs:
        try:
            parsed, report = parse_pdf(read_artifact(artifact), country_code=code, source_url=artifact.source_url, edition_year=refs[artifact.source_url]["edition_year"], retrieved_at=plan["retrieved_at"], as_of=as_of)
        except ValueError as exc:
            failures.append({"url": artifact.source_url, "error": str(exc)}); continue
        rows.extend(parsed); reports.append(report)
    # One source category/month may be repeated in a compiled annual PDF and
    # individual month reports. Prefer the independently captured newest edition
    # only if values are identical; contradictions require explicit review.
    unique = {}
    for row in rows:
        key = (row["Date"], row["SourceDiseaseCode"])
        if key in unique and (unique[key]["Cases"], unique[key]["MissingValueStatus"]) != (row["Cases"], row["MissingValueStatus"]):
            raise ValueError("Conflicting national monthly editions for same source cell")
        unique[key] = row
    if not unique:
        raise ValueError("No national single-month reports could be organized")
    failed = len(failures) + len(plan["errors"]) + sum(r["failed_count"] for r in reports)
    audit = {"status": "partial" if failed else "parsed", "references": len(discovered), "archived_reports": len(pdfs), "parsed_reports": len(reports), "failed_count": failed, "failures": failures, "download_failures": plan["errors"], "excluded": exclusions, "reports": reports, "coverage_complete": not failed, "withdrawal_authority": False}
    return updater.prepare_rows(list(unique.values()), source=source or contract["scope"], source_audit=audit, **options)
